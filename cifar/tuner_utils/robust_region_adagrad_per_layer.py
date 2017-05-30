import numpy as np
import matplotlib.pyplot as plt
from math import ceil, floor
from scipy.optimize import minimize
from tensorflow.python.training import momentum
import tensorflow as tf
import itertools

def running_mean(x, N):
  cumsum = np.cumsum(np.insert(x, 0, 0)) 
  return (cumsum[N:] - cumsum[:-N]) / N 


class OptimizerUnit(object):
  def __init__(self, lr_val, mu_val, clip_thresh_val, alpha=10,
               high_pct=99.5, low_pct=0.5, gamma=0.1, 
               mu_update_interval=10, use_placeholder=True):
    # use placeholder if the graph complain assign op can not be used 
    # after the graph is finalized.
    if use_placeholder:
        self.lr_var = tf.placeholder(tf.float32, shape=() )
        self.mu_var = tf.placeholder(tf.float32, shape=() )
        self.clip_thresh_var = tf.placeholder(tf.float32, shape=() )
    else:
        self.lr_var = tf.Variable(lr_val, trainable=False)
        self.mu_var = tf.Variable(mu_val, trainable=False)
        self.clip_thresh_var = tf.Variable(clip_thresh_val, trainable=False)

    self.lr_val = lr_val
    self.mu_val = mu_val
    self.clip_thresh_val = clip_thresh_val
    self._optimizer = tf.train.MomentumOptimizer(self.lr_var, self.mu_var)

    self._alpha = alpha
    self._gamma = gamma
    self._mu_update_interval = mu_update_interval
    self._curv_list = []
    self._max_curv = None
    self._grad_sum_square = None
    self._iter_id = 0
    self._high_pct = high_pct
    self._low_pct = low_pct
    
    # monitoring code
    self._max_curv_list = []
    self._lr_list = []
    self._mu_list = []
    self._lr_grad_list = []
    # clip_list monitor thresh over lr * clip_thresh
    self._clip_list = []
    self._dr_list = []
    self._id = None
    self._slow_start_iters = 200
    
    # TODO remove for debug
    self._accum_grad_squared_list = []


  def apply_gradients(self, grads_tvars):
    self._grads, self._tvars = zip(*grads_tvars)
    self._grads_clip, self._grads_norm = tf.clip_by_global_norm(self._grads, self.clip_thresh_var)    
    self.apply_grad_op = \
      self._optimizer.apply_gradients(zip(self._grads_clip, self._tvars) )
    return self.apply_grad_op


  def assign_hyper_param(self, lr_val, mu_val, clip_thresh_val):
    lr_op = self.lr_var.assign(lr_val)
    mu_op = self.mu_var.assign(mu_val)
    clip_thresh_op = self.clip_thresh_var.assign(clip_thresh_val / float(lr_val) )
    self.lr_val = lr_val
    self.mu_val = mu_val
    self.clip_thresh_val = clip_thresh_val
    return tf.group(lr_op, mu_op, clip_thresh_op)


  def assign_hyper_param_value(self, lr_val, mu_val, clip_thresh_val):
    self.lr_val = lr_val
    # TODO change back
    self.mu_val = mu_val
    # self.mu_val = 0.0
    self.clip_thresh_val = clip_thresh_val
    return 


  def get_min_max_curvatures(self):
    all_curvatures = self._curv_list
    t=len(all_curvatures)
    W=10
    start = max([0,t-W])
    max_curv=max(all_curvatures[start:t])
    min_curv=min(all_curvatures[start:t])
    return max_curv, min_curv


  def set_alpha(self, alpha):
    self._alpha = alpha


  def set_slow_start_iters(self, slow_start_iters):
    self._slow_start_iters = slow_start_iters

    
  def get_lr(self):
    # lr = self._alpha / np.sqrt(sum(self._curv_list) + 1e-6)
    # lr = self._alpha *(min([1.0, 1/float(np.sqrt(self._slow_start_iters) )**2+1/float(np.sqrt(self._slow_start_iters) )**2*self._iter_id] ) ) / np.sqrt(sum(self._curv_list) + 1e-6)
    lr = self._alpha *(min([1.0, 1/float(self._slow_start_iters)+1/float(self._slow_start_iters)*self._iter_id] ) ) / np.sqrt(sum(self._curv_list) + 1e-6)
    
    print "test alpha ", self._alpha, min([1.0, 1/float(self._slow_start_iters)+1/float(self._slow_start_iters)*self._iter_id] ), np.sqrt(sum(self._curv_list) + 1e-6)
    
    self._accum_grad_squared_list.append(sum(self._curv_list) )
    
    
    return lr


  def get_mu(self):
    high_pct = self._high_pct
    low_pct = self._low_pct
    pct_max = np.percentile(self._grad_sum_square[self._grad_sum_square != 0], high_pct)
    pct_min = np.percentile(self._grad_sum_square[self._grad_sum_square != 0], low_pct) 
    dr = np.sqrt(pct_max / (pct_min + 1e-9) ) 
    mu = ( (np.sqrt(dr) - 1) / (np.sqrt(dr) + 1) )**2
    return dr, mu


  def on_iter_finish(self, sess, grad_vals, use_hyper_op=False):
    i = self._iter_id + 1
    w = i**(-1.0)
    beta_poly = w
    gamma = self._gamma

    # in case there are sparse gradient strutures
    for i, item in enumerate(grad_vals):
      if type(item) is not np.ndarray:
        tmp = np.zeros(item.dense_shape)
        # note here we have duplicated vectors corresponding to the same word
        np.add.at(tmp, item.indices, item.values)
        grad_vals[i] = tmp.copy()


    grad_sum_square_new = np.hstack( [val.ravel()**2 for val in grad_vals] )
    self._curv_list.append(np.sum(grad_sum_square_new) )    

    # this max_curv_new is for clipping
    max_curv_new, _ = self.get_min_max_curvatures()
    # update 
    if self._max_curv == None:
        self._max_curv = max_curv_new
    else:
        self._max_curv = (beta_poly**gamma)*max_curv_new + (1-beta_poly**gamma)*self._max_curv
    if self._grad_sum_square is None:
      self._grad_sum_square = grad_sum_square_new 
    else:
      self._grad_sum_square += grad_sum_square_new
    
    if self._iter_id % self._mu_update_interval == 0 and self._iter_id > 0:
      dr, mu_val = self.get_mu()
      self._dr_list.append(dr)
    else:
      mu_val = self.mu_val
    if self._iter_id >= 0:
        lr_val = self.get_lr()
    else:
      lr_val = self.lr_val

    clip_thresh_val = lr_val * np.sqrt(self._max_curv)
            
    # TODO tidy up capping operation
    if use_hyper_op:
      hyper_op = self.assign_hyper_param(lr_val, min(mu_val, 0.9), min(clip_thresh_val, 1.0) )
    else:
      self.assign_hyper_param_value(lr_val, min(mu_val, 0.9), min(clip_thresh_val, 1.0) )

    self._max_curv_list.append(self._max_curv)
    self._lr_list.append(lr_val)
    self._mu_list.append(min(mu_val, 0.9) )
    self._lr_grad_list.append(lr_val * np.sqrt(self._curv_list[-1] ) )
    # clip_list monitor thresh over lr * clip_thresh
    self._clip_list.append(min(clip_thresh_val, 1.0) )

    self._iter_id += 1
    
    if use_hyper_op:
      return hyper_op
    else:
      return


  def plot_curv(self, log_dir='./'):            
    plt.figure()
    plt.semilogy(self._lr_grad_list, label="lr * grad")
    # plt.semilogy(self._max_curv_list, label="max curv for clip")
    plt.semilogy(self._curv_list, label="curv for clip")
    plt.semilogy(self._accum_grad_squared_list, label="demonimnator")
    # plt.semilogy(self._clip_list, label="clip thresh")
    plt.semilogy(self._lr_list, label="lr")
    plt.semilogy(self._mu_list, label="mu")
    plt.title('LR='+str(self._lr_list[-1] )+' mu='+str(self._mu_list[-1] ) )
    plt.xlabel("iteration")
    plt.grid()
    ax = plt.subplot(111)
    ax.legend(loc='lower left', 
            ncol=2, fancybox=True, shadow=True)
    plt.savefig(log_dir + "/fig_" + str(self._id) + ".png")
    plt.close()
    # save monitoring quantities
    with open(log_dir + "/lr_grad_" + str(self._id) + ".txt", "w") as f:
        np.savetxt(f, np.array(self._lr_grad_list) )
    with open(log_dir + "/max_curv_" + str(self._id) + ".txt", "w") as f:
        np.savetxt(f, np.array(self._max_curv_list) )
    with open(log_dir + "/clip_thresh_" + str(self._id) + ".txt", "w") as f:
        np.savetxt(f, np.array(self._clip_list) )
    with open(log_dir + "/lr_" + str(self._id) + ".txt", "w") as f:
        np.savetxt(f, np.array(self._lr_list) )
    with open(log_dir + "/mu_" + str(self._id) + ".txt", "w") as f:
        np.savetxt(f, np.array(self._mu_list) )  
    with open(log_dir + "/mu_" + str(self._id) + ".txt", "w") as f:
        np.savetxt(f, np.array(self._dr_list) )


class MetaOptimizer(object):
  def __init__(self, lr_vals, mu_vals, clip_thresh_vals, 
               alpha=10, high_pct=0.99, low_pct=0.5,
               gamma=0.5, use_hyper_op=False):
    assert len(lr_vals) == len(mu_vals)
    assert len(mu_vals) == len(clip_thresh_vals)
    self._use_hyper_op = use_hyper_op
    self.clip_thresh_vals = clip_thresh_vals[:]
    self._optimizers = [OptimizerUnit(lr_val, mu_val, clip_thresh_val,
                        use_placeholder=(not use_hyper_op) ) \
                        for lr_val, mu_val, clip_thresh_val in zip(lr_vals, mu_vals, clip_thresh_vals) ]
    for i in range(len(self._optimizers) ):
        self._optimizers[i]._id = i
    self.lr_vars = [optimizer.lr_var for optimizer in self._optimizers]
    self.mu_vars = [optimizer.mu_var for optimizer in self._optimizers]
    self.clip_thresh_vars = [optimizer.clip_thresh_var for optimizer in self._optimizers]
    self._iter_id = 0
    self._loss_list = [] 


  def set_alpha(self, alpha):
    for optimizer in self._optimizers:
        optimizer.set_alpha(alpha)


  def set_slow_start_iters(self, slow_start_iters):
    for optimizer in self._optimizers:
        optimizer.set_slow_start_iters(slow_start_iters)

        
  def apply_gradients(self, grad_tvar_list):
    '''
    Grad_tvar_list is a list. Each list is a list of tuples.
    Each tuple is a (grad, tvar) pair
    '''    
    assert len(grad_tvar_list) == len(self._optimizers)
    assert len(grad_tvar_list) == len(self._optimizers)
    assert len(grad_tvar_list) == len(self._optimizers)
    self._apply_grad_ops = [opt.apply_gradients(grad_tvar) \
      for opt, grad_tvar in zip(self._optimizers, grad_tvar_list) ]
    self.apply_grad_op = tf.group(*self._apply_grad_ops)
    return self.apply_grad_op


  def on_iter_finish(self, sess, grad_vals_list, loss):
    lr_vals = []
    mu_vals = []
    clip_thresh_vals = []
    hyper_ops = []
    use_hyper_op = self._use_hyper_op
    self._loss_list.append(loss)
    for optimizer, grad_vals in zip(self._optimizers, grad_vals_list):
      if use_hyper_op:
        hyper_op = optimizer.on_iter_finish(sess, grad_vals, use_hyper_op)
        hyper_ops.append(hyper_op)
      else:
        optimizer.on_iter_finish(sess, grad_vals, use_hyper_op)
      lr_vals.append(optimizer.lr_val)
      mu_vals.append(optimizer.mu_val)
      clip_thresh_vals.append(optimizer.clip_thresh_val)
      # TODO append monitoring measurements
    self.clip_thresh_vals = clip_thresh_vals[:]
    self._iter_id += 1
    
    if use_hyper_op:
        return tf.group(*hyper_ops)
    else:
        return

    
  def assign_hyper_param(self, lr_vals, mu_vals, clip_thresh_vals):
    assert len(lr_vals) == len(self._optimizers)
    assert len(mu_vals) == len(self._optimizers)
    assert len(clip_thresh_vals) == len(self._optimizers)
    assign_ops = []
    for optimizer, lr_val, mu_val, clip_thresh_val in \
      zip(self._optimizers, lr_vals, mu_vals, clip_thresh_vals):
      assign_ops.append(optimizer.assign_hyper_param(lr_val, mu_val, clip_thresh_val) )
    return tf.group(*assign_ops)


  def assign_hyper_param_value(self, lr_vals, mu_vals, clip_thresh_vals):
    assert len(lr_vals) == len(self._optimizers)
    assert len(mu_vals) == len(self._optimizers)
    assert len(clip_thresh_vals) == len(self._optimizers)
    for optimizer, lr_val, mu_val, clip_thresh_val in \
      zip(self._optimizers, lr_vals, mu_vals, clip_thresh_vals):
      optimizer.assign_hyper_param_value(lr_val, mu_val, clip_thresh_val)
    return


  def plot_curv(self, plot_id=None, log_dir='./'):
    if plot_id == None:
      for i, optimizer in enumerate(self._optimizers):
        print("plot for optimizer " + str(i) )
        optimizer.plot_curv(log_dir=log_dir)
    else:
      for i in plot_id:
        print("plot for optimizer " + str(i) )
        self._optimizers[i].plot_curv(log_dir=log_dir)
    # save the loss
    with open(log_dir + "/loss.txt", "w") as f:
        np.savetxt(f, np.array(self._loss_list) )
    print("ckpt done for iter ", self._iter_id)
    return
            
    
  def get_hyper_feed_dict(self):
    lr_pair = [ (optimizer.lr_var, optimizer.lr_val) for optimizer in self._optimizers]
    mu_pair = [ (optimizer.mu_var, optimizer.mu_val) for optimizer in self._optimizers]
    clip_thresh_pair = [ (optimizer.clip_thresh_var, optimizer.clip_thresh_val) for optimizer in self._optimizers]
    feed_dict = dict(lr_pair + mu_pair + clip_thresh_pair)
    return feed_dict