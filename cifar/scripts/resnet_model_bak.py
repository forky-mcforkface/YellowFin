# Copyright 2016 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""ResNet model.

Related papers:
https://arxiv.org/pdf/1603.05027v2.pdf
https://arxiv.org/pdf/1512.03385v1.pdf
https://arxiv.org/pdf/1605.07146v1.pdf
"""
from collections import namedtuple

import numpy as np
import tensorflow as tf
import sys

from tensorflow.python.training import moving_averages
sys.path.append('../tuner_utils')
# from robust_region_adagrad_per_layer import *
from yellow_fin import *


HParams = namedtuple('HParams',
                     'batch_size, num_classes, min_lrn_rate, lrn_rate, mom, clip_norm_base,'
                     'num_residual_units, use_bottleneck, weight_decay_rate, '
                     'relu_leakiness, optimizer, model_scope')


class ResNet(object):
  """ResNet model."""

  def __init__(self, hps, images, labels, mode):
    """ResNet constructor.

    Args:
      hps: Hyperparameters.
      images: Batches of images. [batch_size, image_size, image_size, 3]
      labels: Batches of labels. [batch_size, num_classes]
      mode: One of 'train' and 'eval'.
    """
    self.hps = hps
    self._images = images
    self.labels = labels
    self.mode = mode

    self._extra_train_ops = []
    
    self.relu_output = []
    

  def build_graph(self):
    """Build a whole graph for the model."""
    self.global_step = tf.contrib.framework.get_or_create_global_step()
    self._build_model()
    if self.mode == 'train':
      self._build_train_op()
    else:
      self.trainable_variables =  tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.hps.model_scope)


  def _stride_arr(self, stride):
    """Map a stride scalar to the stride array for tf.nn.conv2d."""
    return [1, stride, stride, 1]

  def _build_model(self):
    """Build the core model within the graph."""
    with tf.variable_scope('init'):
      x = self._images
      x = self._conv('init_conv', x, 3, 3, 16, self._stride_arr(1))
      # x = self._conv('init_conv', x, 3, 3, 4, self._stride_arr(1))

    strides = [1, 2, 2]
    activate_before_residual = [True, False, False]
    if self.hps.use_bottleneck:
      res_func = self._bottleneck_residual
      filters = [16, 64, 128, 256]
    else:
      res_func = self._residual
      filters = [16, 16, 32, 64]
      # filters = [4, 4, 8, 16]
      # Uncomment the following codes to use w28-10 wide residual network.
      # It is more memory efficient than very deep residual network and has
      # comparably good performance.
      # https://arxiv.org/pdf/1605.07146v1.pdf
      # filters = [16, 160, 320, 640]
      # Update hps.num_residual_units to 9

    with tf.variable_scope('unit_1_0'):
      x = res_func(x, filters[0], filters[1], self._stride_arr(strides[0]),
                   activate_before_residual[0])
    for i in xrange(1, self.hps.num_residual_units):
      with tf.variable_scope('unit_1_%d' % i):
        x = res_func(x, filters[1], filters[1], self._stride_arr(1), False)

    with tf.variable_scope('unit_2_0'):
      x = res_func(x, filters[1], filters[2], self._stride_arr(strides[1]),
                   activate_before_residual[1])
    for i in xrange(1, self.hps.num_residual_units):
      with tf.variable_scope('unit_2_%d' % i):
        x = res_func(x, filters[2], filters[2], self._stride_arr(1), False)

    with tf.variable_scope('unit_3_0'):
      x = res_func(x, filters[2], filters[3], self._stride_arr(strides[2]),
                   activate_before_residual[2])
    for i in xrange(1, self.hps.num_residual_units):
      with tf.variable_scope('unit_3_%d' % i):
        x = res_func(x, filters[3], filters[3], self._stride_arr(1), False)

    with tf.variable_scope('unit_last'):
      x = self._batch_norm('final_bn', x)
      x = self._relu(x, self.hps.relu_leakiness)
      x = self._global_avg_pool(x)

    with tf.variable_scope('logit'):
      logits = self._fully_connected(x, self.hps.num_classes)
      self.predictions = tf.nn.softmax(logits)

    with tf.variable_scope('costs'):
      xent = tf.nn.softmax_cross_entropy_with_logits(
          logits, self.labels)
      self.cost = tf.reduce_mean(xent, name='xent')
      self.cost += self._decay()

      # tf.summary.scalar('cost', self.cost)

  def _build_train_op(self):
    """Build training specific ops for the graph."""
    # self.lrn_rate = tf.constant(self.hps.lrn_rate, tf.float32)
    # self.lrn_rate = tf.Variable(self.hps.lrn_rate, trainable=False, dtype=tf.float32)
    # self.mom = tf.Variable(self.hps.mom, trainable=False, dtype=tf.float32)
    # self.clip_norm = tf.Variable(self.hps.clip_norm_base / self.hps.lrn_rate, trainable=False, dtype=tf.float32)
    self.lrn_rate = tf.placeholder(tf.float32, shape=[] )
    self.mom = tf.placeholder(tf.float32, shape=[] )
    self.clip_norm = tf.placeholder(tf.float32, shape=[] )

    # tf.summary.scalar('learning rate', self.lrn_rate)

    self.trainable_variables =  tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.hps.model_scope)
    self.grads = tf.gradients(self.cost, self.trainable_variables)
    self.grads_clip, self.global_norm = tf.clip_by_global_norm(self.grads, self.clip_norm)
    
    if "meta" not in self.hps.optimizer:
      if self.hps.optimizer == 'sgd':
        optimizer = tf.train.GradientDescentOptimizer(self.lrn_rate)
      elif self.hps.optimizer == 'mom':
        optimizer = tf.train.MomentumOptimizer(self.lrn_rate, self.mom)
      elif self.hps.optimizer == 'adam':
        print "adam optimizer"
        optimizer = tf.train.AdamOptimizer(self.lrn_rate)

      apply_op = optimizer.apply_gradients(
          zip(self.grads_clip, self.trainable_variables),
          global_step=self.global_step, name='train_step')

    else:
      if self.hps.optimizer == 'meta':
        pass
      elif self.hps.optimizer == 'meta-per-layer':
        print "meta-per-layer optimizer"
        n_vars = len(self.grads)
        dummy_lr_vals = (1.0 * np.ones( (n_vars, ) ) ).tolist()
        dummy_mu_vals = (0.0 * np.ones( (n_vars, ) ) ).tolist()
        dummy_thresh_vals = (1.0 * np.ones( (n_vars, ) ) ).tolist()
        grads_tvars = [ [ (grad, tvar), ] for grad, tvar in zip(self.grads, self.trainable_variables) ]
        self.optimizer = MetaOptimizer(dummy_lr_vals, dummy_mu_vals, dummy_thresh_vals)
        apply_op = self.optimizer.apply_gradients(grads_tvars)
      elif self.hps.optimizer == 'meta-bundle':
        print "meta-bundle optimizer"
        grads_tvars = []

        for key, bundle in itertools.groupby(zip(self.grads, self.trainable_variables),\
                                              lambda x: x[1].name.split("/")[1].split("/")[0] ):
            grads_tvars.append(list(bundle) )
        n_bundles = len(grads_tvars)      
        dummy_lr_vals = (1.0 * np.ones( (n_bundles, ) ) ).tolist()
        dummy_mu_vals = (0.0 * np.ones( (n_bundles, ) ) ).tolist()
        dummy_thresh_vals = (1.0 * np.ones( (n_bundles, ) ) ).tolist()
        self.optimizer = MetaOptimizer(dummy_lr_vals, dummy_mu_vals, dummy_thresh_vals)
        apply_op = self.optimizer.apply_gradients(grads_tvars)
      else:
        raise Exception("the specific optimizer is not supported.")

    train_ops = [apply_op] + self._extra_train_ops
    self.train_op = tf.group(*train_ops)


  # TODO(xpan): Consider batch_norm in contrib/layers/python/layers/layers.py
  def _batch_norm(self, name, x):
    """Batch normalization."""
    with tf.variable_scope(name):
      params_shape = [x.get_shape()[-1]]

      beta = tf.get_variable(
          'beta', params_shape, tf.float32,
          initializer=tf.constant_initializer(0.0, tf.float32))
      gamma = tf.get_variable(
          'gamma', params_shape, tf.float32,
          initializer=tf.constant_initializer(1.0, tf.float32))

      if self.mode == 'train':
        mean, variance = tf.nn.moments(x, [0, 1, 2], name='moments')

        moving_mean = tf.get_variable(
            'moving_mean', params_shape, tf.float32,
            initializer=tf.constant_initializer(0.0, tf.float32),
            trainable=False)
        moving_variance = tf.get_variable(
            'moving_variance', params_shape, tf.float32,
            initializer=tf.constant_initializer(1.0, tf.float32),
            trainable=False)

        self._extra_train_ops.append(moving_averages.assign_moving_average(
            moving_mean, mean, 0.9))
        self._extra_train_ops.append(moving_averages.assign_moving_average(
            moving_variance, variance, 0.9))
      else:
        mean = tf.get_variable(
            'moving_mean', params_shape, tf.float32,
            initializer=tf.constant_initializer(0.0, tf.float32),
            trainable=False)
        variance = tf.get_variable(
            'moving_variance', params_shape, tf.float32,
            initializer=tf.constant_initializer(1.0, tf.float32),
            trainable=False)
        
        # tf.summary.histogram(mean.op.name, mean)
        # tf.summary.histogram(variance.op.name, variance)

      # elipson used to be 1e-5. Maybe 0.001 solves NaN problem in deeper net.
      y = tf.nn.batch_normalization(
          x, mean, variance, beta, gamma, 0.001)
      y.set_shape(x.get_shape())
      return y

  def _residual(self, x, in_filter, out_filter, stride,
                activate_before_residual=False):
    """Residual unit with 2 sub layers."""
    if activate_before_residual:
      with tf.variable_scope('shared_activation'):
        x = self._batch_norm('init_bn', x)
        x = self._relu(x, self.hps.relu_leakiness)
        orig_x = x
    else:
      with tf.variable_scope('residual_only_activation'):
        orig_x = x
        x = self._batch_norm('init_bn', x)
        x = self._relu(x, self.hps.relu_leakiness)

    with tf.variable_scope('sub1'):
      x = self._conv('conv1', x, 3, in_filter, out_filter, stride)

    with tf.variable_scope('sub2'):
      x = self._batch_norm('bn2', x)
      x = self._relu(x, self.hps.relu_leakiness)
      x = self._conv('conv2', x, 3, out_filter, out_filter, [1, 1, 1, 1])

    with tf.variable_scope('sub_add'):
      if in_filter != out_filter:
        orig_x = tf.nn.avg_pool(orig_x, stride, stride, 'VALID')
        orig_x = tf.pad(
            orig_x, [[0, 0], [0, 0], [0, 0],
                     [(out_filter-in_filter)//2, (out_filter-in_filter)//2]])
      x += orig_x

    tf.logging.info('image after unit %s', x.get_shape())
    return x


  # def _residual(self, x, in_filter, out_filter, stride,
  #               activate_before_residual=False, check_shape=True):
  #   """Residual unit with 2 sub layers."""
  #   if activate_before_residual:
  #     with tf.variable_scope('shared_activation'):
  #       x = self._batch_norm('init_bn', x)
  #       x = self._relu(x, self.hps.relu_leakiness)
  #       orig_x = x
  #   else:
  #     with tf.variable_scope('residual_only_activation'):
  #       orig_x = x
  #       x = self._batch_norm('init_bn', x)
  #       x = self._relu(x, self.hps.relu_leakiness)

  #   with tf.variable_scope('sub1'):
  #     x = self._conv('conv1', x, 3, in_filter, out_filter, stride)

  #   with tf.variable_scope('sub2'):

  #     print "bn2 before ", x.get_shape()

  #     x = self._batch_norm('bn2', x)
  #     x = self._relu(x, self.hps.relu_leakiness)
  #     x = self._conv('conv2', x, 3, out_filter, out_filter, [1, 1, 1, 1])

  #   with tf.variable_scope('sub_add'):
  #     if in_filter != out_filter:
  #       orig_x = tf.nn.avg_pool(orig_x, stride, stride, 'VALID')
  #       orig_x = tf.pad(
  #           orig_x, [[0, 0], [0, 0], [0, 0],
  #                    [(out_filter-in_filter)//2, (out_filter-in_filter)//2]])
  #     x += orig_x

  #   tf.logging.info('image after unit %s', x.get_shape())
  #   return x


  def _bottleneck_residual(self, x, in_filter, out_filter, stride,
                           activate_before_residual=False):
    """Bottleneck resisual unit with 3 sub layers."""
    if activate_before_residual:
      with tf.variable_scope('common_bn_relu'):
        x = self._batch_norm('init_bn', x)
        x = self._relu(x, self.hps.relu_leakiness)
        orig_x = x
    else:
      with tf.variable_scope('residual_bn_relu'):
        orig_x = x
        x = self._batch_norm('init_bn', x)
        x = self._relu(x, self.hps.relu_leakiness)

    with tf.variable_scope('sub1'):
      x = self._conv('conv1', x, 1, in_filter, out_filter/4, stride)

    with tf.variable_scope('sub2'):
      x = self._batch_norm('bn2', x)
      x = self._relu(x, self.hps.relu_leakiness)
      x = self._conv('conv2', x, 3, out_filter/4, out_filter/4, [1, 1, 1, 1])

    with tf.variable_scope('sub3'):
      x = self._batch_norm('bn3', x)
      x = self._relu(x, self.hps.relu_leakiness)
      x = self._conv('conv3', x, 1, out_filter/4, out_filter, [1, 1, 1, 1])

    with tf.variable_scope('sub_add'):
      if in_filter != out_filter:
        orig_x = self._conv('project', orig_x, 1, in_filter, out_filter, stride)
      x += orig_x

    tf.logging.info('image after unit %s', x.get_shape())
    return x

  def _decay(self):
    """L2 weight decay loss."""
    costs = []
    # for var in self.trainable_variables:
    for var in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope=self.hps.model_scope):
      if var.op.name.find(r'DW') > 0:
        costs.append(tf.nn.l2_loss(var))
        # tf.histogram_summary(var.op.name, var)

    return tf.mul(self.hps.weight_decay_rate, tf.add_n(costs))

  def _conv(self, name, x, filter_size, in_filters, out_filters, strides):
    """Convolution."""
    with tf.variable_scope(name):
      n = filter_size * filter_size * out_filters
      kernel = tf.get_variable(
          'DW', [filter_size, filter_size, in_filters, out_filters],
          tf.float32, initializer=tf.random_normal_initializer(
              stddev=np.sqrt(2.0/n)))
      return tf.nn.conv2d(x, kernel, strides, padding='SAME')

  # def _relu(self, x, leakiness=0.0):
  #   """Relu, with optional leaky support."""
  #   return tf.select(tf.less(x, 0.0), leakiness * x, x, name='leaky_relu')
  def _relu(self, x, leakiness=0.0):
    """Relu, with optional leaky support."""
    output = tf.select(tf.less(x, 0.0), leakiness * x, x, name='leaky_relu')
    self.relu_output.append(output)
    return output

  def _fully_connected(self, x, out_dim):
    """FullyConnected layer for final output."""
    x = tf.reshape(x, [self.hps.batch_size, -1])
    w = tf.get_variable(
        'DW', [x.get_shape()[1], out_dim],
        initializer=tf.uniform_unit_scaling_initializer(factor=1.0))
    b = tf.get_variable('biases', [out_dim],
                        initializer=tf.constant_initializer())
    return tf.nn.xw_plus_b(x, w, b)

  def _global_avg_pool(self, x):
    assert x.get_shape().ndims == 4
    return tf.reduce_mean(x, [1, 2])