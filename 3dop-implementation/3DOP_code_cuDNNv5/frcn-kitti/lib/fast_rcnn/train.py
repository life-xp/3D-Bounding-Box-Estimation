# --------------------------------------------------------
# Fast R-CNN
# Copyright (c) 2015 Microsoft
# Licensed under The MIT License [see LICENSE for details]
# Written by Ross Girshick
# --------------------------------------------------------

"""Train a Fast R-CNN network."""

import caffe
from fast_rcnn.config import cfg
import roi_data_layer.roidb as rdl_roidb
from utils.timer import Timer
import numpy as np
import os
import google.protobuf.text_format
from caffe.proto import caffe_pb2
import google.protobuf as pb2

class SolverWrapper(object):
    """A simple wrapper around Caffe's solver.
    This wrapper gives us control over he snapshotting process, which we
    use to unnormalize the learned bounding-box regression weights.
    """
    def copy_layer_params(self, net_params, src_layer_name, det_layer_name):
        if net_params.has_key(src_layer_name) and net_params.has_key(det_layer_name):
            assert len(net_params[src_layer_name]) == len(net_params[det_layer_name])
            for i in xrange(len(net_params[src_layer_name])):
                net_params[det_layer_name][i].data[...] = net_params[src_layer_name][i].data

    def __init__(self, solver_prototxt, roidb, output_dir,
                 pretrained_model=None):
        """Initialize the SolverWrapper."""
        self.output_dir = output_dir

        print 'Computing bounding-box regression targets...'
        self.bbox_means, self.bbox_stds = \
                rdl_roidb.add_bbox_regression_targets(roidb)
        print 'done'

        self.solver = caffe.SGDSolver(solver_prototxt)
        if pretrained_model is not None:
            print ('Loading pretrained model '
                   'weights from {:s}').format(pretrained_model)
            self.solver.net.copy_from(pretrained_model)
            # initialize context branch using fc6/fc7 weights
            net = self.solver.net
            self.copy_layer_params(net.params, 'fc6', 'fc6_context')
            self.copy_layer_params(net.params, 'fc7', 'fc7_context')

            
            # -- spbob - start
            #self.copy_layer_params(net.params, 'conv1_1', 'conv1_1_depth')
            self.copy_layer_params(net.params, 'conv1_2', 'conv1_2_depth')
            self.copy_layer_params(net.params, 'conv2_1', 'conv2_1_depth')
            self.copy_layer_params(net.params, 'conv2_2', 'conv2_2_depth')
            self.copy_layer_params(net.params, 'conv3_1', 'conv3_1_depth')
            self.copy_layer_params(net.params, 'conv3_2', 'conv3_2_depth')
            self.copy_layer_params(net.params, 'conv3_3', 'conv3_3_depth')
            self.copy_layer_params(net.params, 'conv4_1', 'conv4_1_depth')
            self.copy_layer_params(net.params, 'conv4_2', 'conv4_2_depth')
            self.copy_layer_params(net.params, 'conv4_3', 'conv4_3_depth')
            self.copy_layer_params(net.params, 'conv5_1', 'conv5_1_depth')
            self.copy_layer_params(net.params, 'conv5_2', 'conv5_2_depth')
            self.copy_layer_params(net.params, 'conv5_3', 'conv5_3_depth')
            self.copy_layer_params(net.params, 'fc6', 'fc6_depth')
            self.copy_layer_params(net.params, 'fc7', 'fc7_depth')
            self.copy_layer_params(net.params, 'fc6', 'fc6_context_depth')
            self.copy_layer_params(net.params, 'fc7', 'fc7_context_depth')
            # -- spbob - end
            

        self.solver_param = caffe_pb2.SolverParameter()
        with open(solver_prototxt, 'rt') as f:
            pb2.text_format.Merge(f.read(), self.solver_param)

        self.solver.net.layers[0].set_roidb(roidb)

    def snapshot(self):
        """Take a snapshot of the network after unnormalizing the learned
        bounding-box regression weights. This enables easy use at test-time.
        """
        net = self.solver.net

        if cfg.TRAIN.BBOX_REG:
            # save original values
            orig_0 = net.params['bbox_pred'][0].data.copy()
            orig_1 = net.params['bbox_pred'][1].data.copy()

            # scale and shift with bbox reg unnormalization; then save snapshot
            net.params['bbox_pred'][0].data[...] = \
                    (net.params['bbox_pred'][0].data *
                     self.bbox_stds[:, np.newaxis])
            net.params['bbox_pred'][1].data[...] = \
                    (net.params['bbox_pred'][1].data *
                     self.bbox_stds + self.bbox_means)

        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

        infix = ('_' + cfg.TRAIN.SNAPSHOT_INFIX
                 if cfg.TRAIN.SNAPSHOT_INFIX != '' else '')
        filename = (self.solver_param.snapshot_prefix + infix +
                    '_iter_{:d}'.format(self.solver.iter) + '.caffemodel')
        filename = os.path.join(self.output_dir, filename)

        net.save(str(filename))
        print 'Wrote snapshot to: {:s}'.format(filename)

        if cfg.TRAIN.BBOX_REG:
            # restore net to original state
            net.params['bbox_pred'][0].data[...] = orig_0
            net.params['bbox_pred'][1].data[...] = orig_1

    def train_model(self, max_iters):
        """Network training loop."""
        last_snapshot_iter = -1
        timer = Timer()

        net = self.solver.net
        while self.solver.iter < max_iters:
            # Make one SGD update
            timer.tic()
            self.solver.step(1)
            timer.toc()
            if self.solver.iter % (10 * self.solver_param.display) == 0:
                print 'speed: {:.3f}s / iter'.format(timer.average_time)

            if self.solver.iter % cfg.TRAIN.SNAPSHOT_ITERS == 0:
                last_snapshot_iter = self.solver.iter
                self.snapshot()

        if last_snapshot_iter != self.solver.iter:
            self.snapshot()

def get_training_roidb(imdb):
    """Returns a roidb (Region of Interest database) for use in training."""
    if cfg.TRAIN.USE_FLIPPED:
        print 'Appending horizontally-flipped training examples...'
        imdb.append_flipped_images()
        print 'done'

    print 'Preparing training data...'
    rdl_roidb.prepare_roidb(imdb)
    print 'done'

    return imdb.roidb

def filter_roidb(roidb):
    """Remove roidb entries that have no usable RoIs."""

    def is_valid(entry):
        # Valid images have:
        #   (1) At least one foreground roi AND
        #   (2) At least one background roi
        overlaps = entry['max_overlaps']
        # find boxes with sufficient overlap excluding ground truth boxes
        fg_inds = np.where((overlaps >= cfg.TRAIN.FG_THRESH) &
                           (overlaps < 1.0))[0]
        # Select background RoIs as those within [BG_THRESH_LO, BG_THRESH_HI)
        bg_inds = np.where((overlaps < cfg.TRAIN.BG_THRESH_HI) &
                           (overlaps >= cfg.TRAIN.BG_THRESH_LO))[0]
        # image is only valid if such boxes exist
        valid = len(fg_inds) > 0 and len(bg_inds) > 0
        return valid

    num = len(roidb)
    filtered_roidb = [entry for entry in roidb if is_valid(entry)]
    num_after = len(filtered_roidb)
    print 'Filtered {} roidb entries: {} -> {}'.format(num - num_after,
                                                       num, num_after)
    return filtered_roidb

def train_net(solver_prototxt, roidb, output_dir,
              pretrained_model=None, max_iters=40000):
    """Train a Fast R-CNN network."""
    roidb = filter_roidb(roidb)
    print "here"
    for key in roidb[1]:
        print "key is:", key
    sw = SolverWrapper(solver_prototxt, roidb, output_dir,
                       pretrained_model=pretrained_model)

    print 'Solving...'
    sw.train_model(max_iters)
    print 'done solving'
