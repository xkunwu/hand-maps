import os
import sys
from importlib import import_module
import numpy as np
import tensorflow as tf
from tensorflow.contrib import slim
import progressbar
import h5py
import matplotlib.pyplot as mpplot
from cv2 import resize as cv2resize
from train.batch_allot import batch_allot

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(BASE_DIR, os.pardir))
sys.path.append(BASE_DIR)
file_pack = getattr(
    import_module('utils.coder'),
    'file_pack'
)
iso_rect = getattr(
    import_module('utils.iso_boxes'),
    'iso_rect'
)
iso_aabb = getattr(
    import_module('utils.iso_boxes'),
    'iso_aabb'
)


class base_regre(object):
    """ This class holds baseline training approach using plain regression.
    """
    def __init__(self, args):
        self.net_rank = 2
        self.net_type = 'poser'
        self.name_desc = self.__class__.__name__ + args.model_desc
        self.crop_size = 128
        self.anchor_num = 16
        self.crop_range = 800.
        self.num_channel = 1
        self.num_appen = 7
        self.batch_allot = batch_allot
        # receive arguments
        self.args = args
        self.prepare_dir = args.prepare_dir
        self.appen_train = os.path.join(
            self.prepare_dir, 'train_{}'.format(
                self.__class__.__name__))
        self.appen_test = os.path.join(
            self.prepare_dir, 'test_{}'.format(
                self.__class__.__name__))
        self.predict_dir = args.predict_dir
        self.predict_file = os.path.join(
            self.predict_dir, 'predict_{}'.format(
                self.name_desc))
        self.batch_size = args.batch_size
        self.ckpt_path = os.path.join(
            args.out_dir, 'log', 'blinks',
            self.name_desc, 'model.ckpt')

    def tweak_arguments(self, args):
        args.crop_size = self.crop_size
        args.anchor_num = self.anchor_num
        args.crop_range = self.crop_range

    def start_train(self, filepack):
        self.store_file = filepack.push_h5(self.appen_train)
        self.store_size = self.store_file['index'].shape[0]

    def start_epoch_train(self, split_beg, split_end):
        self.batch_beg = split_end
        self.split_end = split_beg + self.store_size

    def start_epoch_valid(self, split_beg, split_end):
        self.batch_beg = split_beg
        self.split_end = split_end \
            if 0 != split_end \
            else self.store_size

    def fetch_batch(self, fetch_size=None):
        if fetch_size is None:
            fetch_size = self.batch_size
        batch_end = self.batch_beg + fetch_size
        if batch_end >= self.store_size:
            self.batch_beg = batch_end
            batch_end = self.batch_beg + fetch_size
            self.split_end -= self.store_size
        # print(self.batch_beg, batch_end, self.split_end)
        if batch_end >= self.split_end:
            return None
        batch_data = {
            'batch_index': self.store_file['index'][self.batch_beg:batch_end, ...],
            'batch_frame': self.store_file['frame'][self.batch_beg:batch_end, ...],
            'batch_poses': self.store_file['poses'][self.batch_beg:batch_end, ...],
            'batch_resce': self.store_file['resce'][self.batch_beg:batch_end, ...]
        }
        self.batch_beg = batch_end
        return batch_data

    def end_train(self):
        pass

    def start_evaluate(self, filepack):
        self.store_file = filepack.push_h5(self.appen_test)
        self.store_size = self.store_file['index'].shape[0]
        self.batch_beg = 0
        self.split_end = self.store_size
        return filepack.write_file(self.predict_file)

    def evaluate_batch(self, writer, batch_data, pred_val):
        self.provider.write2d(
            writer, self.yanker, self.caminfo,
            batch_data['batch_index'], batch_data['batch_resce'],
            pred_val
        )

    def end_evaluate(self, thedata, args):
        mpplot.figure(figsize=(2 * 5, 1 * 5))
        args.data_draw.draw_prediction_poses(
            thedata,
            thedata.training_images,
            thedata.training_annot_test,
            self.predict_file
        )
        fname = 'detection_{}.png'.format(self.name_desc)
        mpplot.savefig(os.path.join(self.predict_dir, fname))
        error_maxj = self.evaluater.evaluate_poses(
            self.caminfo, self.name_desc,
            self.predict_dir, self.predict_file)
        self.logger.info('maximal per-joint mean error: {}'.format(
            error_maxj
        ))

    def prepare_data(self, thedata, args,
                     batchallot, file_annot, name_appen):
        num_line = int(sum(1 for line in file_annot))
        file_annot.seek(0)
        batchallot.allot(num_line)
        store_size = batchallot.store_size
        num_stores = int(np.ceil(float(num_line) / store_size))
        self.logger.debug(
            'preparing data [{}]: {:d} lines (producing {:.4f} GB for store size {:d}) ...'.format(
                self.__class__.__name__, num_line,
                float(batchallot.store_bytes) / (2 << 30),
                store_size))
        timerbar = progressbar.ProgressBar(
            maxval=num_stores,
            widgets=[
                progressbar.Percentage(),
                ' ', progressbar.Bar('=', '[', ']'),
                ' ', progressbar.ETA()]
        ).start()
        image_size = self.crop_size
        out_dim = self.out_dim
        num_channel = self.num_channel
        num_appen = self.num_appen
        with h5py.File(os.path.join(self.prepare_dir, name_appen), 'w') as h5file:
            h5file.create_dataset(
                'index',
                (num_line, 1),
                compression='lzf',
                dtype=np.int32
            )
            h5file.create_dataset(
                'frame',
                (num_line,
                    image_size, image_size,
                    num_channel),
                chunks=(1,
                        image_size, image_size,
                        num_channel),
                compression='lzf',
                # dtype=np.float32)
                dtype=float)
            h5file.create_dataset(
                'poses',
                (num_line, out_dim),
                compression='lzf',
                # dtype=np.float32)
                dtype=float)
            h5file.create_dataset(
                'resce',
                (num_line, num_appen),
                compression='lzf',
                # dtype=np.float32)
                dtype=float)
            bi = 0
            store_beg = 0
            while True:
                resline = self.provider.puttensor_mt(
                    file_annot, self.provider_worker,
                    self.image_dir, thedata, batchallot
                )
                if 0 > resline:
                    break
                h5file['index'][store_beg:store_beg + resline, ...] = \
                    batchallot.batch_index[0:resline, ...]
                h5file['frame'][store_beg:store_beg + resline, ...] = \
                    batchallot.batch_frame[0:resline, ...]
                h5file['poses'][store_beg:store_beg + resline, ...] = \
                    batchallot.batch_poses[0:resline, ...]
                h5file['resce'][store_beg:store_beg + resline, ...] = \
                    batchallot.batch_resce[0:resline, ...]
                timerbar.update(bi)
                bi += 1
                store_beg += resline
        timerbar.finish()

    def check_dir(self, thedata, args):
        first_run = False
        if (
                (not os.path.exists(self.appen_train)) or
                (not os.path.exists(self.appen_test))
        ):
            first_run = True
        if not first_run:
            return
        from timeit import default_timer as timer
        from datetime import timedelta
        time_s = timer()
        batchallot = self.batch_allot(
            self.batch_size, self.crop_size, self.out_dim,
            self.num_channel, self.num_appen)
        with file_pack() as filepack:
            file_annot = filepack.push_file(thedata.training_annot_train)
            self.prepare_data(thedata, args, batchallot, file_annot, self.appen_train)
        with file_pack() as filepack:
            file_annot = filepack.push_file(thedata.training_annot_test)
            self.prepare_data(thedata, args, batchallot, file_annot, self.appen_test)
        time_e = str(timedelta(seconds=timer() - time_s))
        self.logger.info('data prepared [{}], time: {}'.format(
            self.__class__.__name__, time_e))

    def receive_data(self, thedata, args):
        """ Receive parameters specific to the data """
        self.logger = args.logger
        self.out_dim = thedata.join_num * 3
        self.image_dir = thedata.training_images
        self.caminfo = thedata
        self.region_size = thedata.region_size
        self.provider = args.data_provider
        self.evaluater = args.data_eval
        self.provider_worker = self.provider.prow_cropped
        self.yanker = self.provider.yank_cropped

    def draw_random(self, thedata, args):
        with h5py.File(self.appen_train, 'r') as h5file:
            store_size = h5file['index'].shape[0]
            frame_id = np.random.choice(store_size)
            # frame_id = 0
            img_id = h5file['index'][frame_id, 0]
            frame_h5 = np.squeeze(h5file['frame'][frame_id, ...], -1)
            poses_h5 = h5file['poses'][frame_id, ...].reshape(-1, 3)
            resce_h5 = h5file['resce'][frame_id, ...]
            print(np.min(frame_h5), np.max(frame_h5))
            print(np.histogram(frame_h5, range=(1e-4, np.max(frame_h5))))
            print(np.min(poses_h5, axis=0), np.max(poses_h5, axis=0))
            print(resce_h5)

        print('[{}] drawing image #{:d}'.format(self.name_desc, img_id))
        resce2 = resce_h5[0:3]
        resce3 = resce_h5[3:7]
        mpplot.subplots(nrows=2, ncols=2, figsize=(2 * 5, 2 * 5))

        mpplot.subplot(2, 2, 3)
        mpplot.gca().set_title('test storage read')
        sizel = np.floor(resce2[0]).astype(int)
        resce_cp = np.copy(resce2)
        resce_cp[0] = 1
        mpplot.imshow(
            cv2resize(frame_h5, (sizel, sizel)),
            cmap='bone')
        pose_raw = args.data_ops.local_to_raw(poses_h5, resce3)
        args.data_draw.draw_pose2d(
            thedata,
            args.data_ops.raw_to_2d(pose_raw, thedata, resce_cp)
        )

        mpplot.subplot(2, 2, 4)
        mpplot.gca().set_title('test output')
        img_name = args.data_io.index2imagename(img_id)
        img = args.data_io.read_image(os.path.join(self.image_dir, img_name))
        mpplot.imshow(img, cmap='bone')
        pose_raw = self.yanker(poses_h5, resce_h5, self.caminfo)
        args.data_draw.draw_pose2d(
            thedata,
            args.data_ops.raw_to_2d(pose_raw, thedata)
        )
        rect = iso_rect()
        rect.load(resce2)
        rect.draw()

        mpplot.subplot(2, 2, 1)
        mpplot.gca().set_title('test input')
        annot_line = args.data_io.get_line(
            thedata.training_annot_cleaned, img_id)
        img_name, pose_raw = args.data_io.parse_line_annot(annot_line)
        img = args.data_io.read_image(os.path.join(self.image_dir, img_name))
        mpplot.imshow(img, cmap='bone')
        args.data_draw.draw_pose2d(
            thedata,
            args.data_ops.raw_to_2d(pose_raw, thedata))

        mpplot.subplot(2, 2, 2)
        mpplot.gca().set_title('test storage write')
        img_name, frame, poses, resce = self.provider_worker(
            annot_line, self.image_dir, thedata)
        frame = np.squeeze(frame, axis=-1)
        poses = poses.reshape(-1, 3)
        if (
                (1e-4 < np.linalg.norm(frame_h5 - frame)) or
                (1e-4 < np.linalg.norm(poses_h5 - poses))
        ):
            print(np.linalg.norm(frame_h5 - frame))
            print(np.linalg.norm(poses_h5 - poses))
            print('ERROR - h5 storage corrupted!')
        resce2 = resce[0:3]
        resce3 = resce[3:7]
        sizel = np.floor(resce2[0]).astype(int)
        resce_cp = np.copy(resce2)
        resce_cp[0] = 1
        mpplot.imshow(
            cv2resize(frame, (sizel, sizel)),
            cmap='bone')
        pose_raw = args.data_ops.local_to_raw(poses, resce3)
        args.data_draw.draw_pose2d(
            thedata,
            args.data_ops.raw_to_2d(pose_raw, thedata, resce_cp)
        )

        mpplot.savefig(os.path.join(
            args.predict_dir,
            'draw_{}.png'.format(self.name_desc)))
        mpplot.show()

    def get_model(
            self, input_tensor, is_training,
            scope=None, final_endpoint='stage_out'):
        """ input_tensor: BxHxWxC
            out_dim: BxJ, where J is flattened 3D locations
        """
        end_points = {}
        self.end_point_list = []

        def add_and_check_final(name, net):
            end_points[name] = net
            return name == final_endpoint

        with tf.variable_scope(
                scope, self.name_desc, [input_tensor]):
            with slim.arg_scope(
                    [slim.batch_norm, slim.dropout],
                    is_training=is_training), \
                slim.arg_scope(
                    [slim.fully_connected],
                    weights_regularizer=slim.l2_regularizer(0.00004),
                    biases_regularizer=slim.l2_regularizer(0.00004),
                    activation_fn=None, normalizer_fn=None), \
                slim.arg_scope(
                    [slim.max_pool2d, slim.avg_pool2d],
                    stride=1, padding='SAME'), \
                slim.arg_scope(
                    [slim.conv2d],
                    stride=1, padding='SAME',
                    activation_fn=tf.nn.relu,
                    weights_regularizer=slim.l2_regularizer(0.00004),
                    biases_regularizer=slim.l2_regularizer(0.00004),
                    normalizer_fn=slim.batch_norm):
                with tf.variable_scope('stage128'):
                    sc = 'stage128_image'
                    net = slim.conv2d(
                        input_tensor, 16, 3, scope='conv128_3x3_1')
                    net = slim.conv2d(
                        net, 16, 3, stride=2, scope='conv128_3x3_2')
                    net = slim.max_pool2d(
                        net, 3, scope='maxpool128_3x3_1')
                    net = slim.conv2d(
                        net, 32, 3, scope='conv64_3x3_1')
                    net = slim.max_pool2d(
                        net, 3, stride=2, scope='maxpool64_3x3_2')
                    self.end_point_list.append(sc)
                    if add_and_check_final(sc, net):
                        return net, end_points
                with tf.variable_scope('stage32'):
                    sc = 'stage32_image'
                    net = slim.conv2d(
                        net, 64, 3, scope='conv32_3x3_1')
                    net = slim.max_pool2d(
                        net, 3, stride=2, scope='maxpool32_3x3_2')
                    self.end_point_list.append(sc)
                    if add_and_check_final(sc, net):
                        return net, end_points
                with tf.variable_scope('stage16'):
                    sc = 'stage16_image'
                    net = slim.conv2d(
                        net, 128, 3, scope='conv16_3x3_1')
                    net = slim.max_pool2d(
                        net, 3, stride=2, scope='maxpool16_3x3_2')
                    self.end_point_list.append(sc)
                    if add_and_check_final(sc, net):
                        return net, end_points
                with tf.variable_scope('stage8'):
                    sc = 'stage_out'
                    net = slim.avg_pool2d(
                        net, 5, stride=3, padding='VALID',
                        scope='avgpool8_5x5_3')
                    self.end_point_list.append('avgpool8_5x5_3')
                    if add_and_check_final('avgpool8_5x5_3', net):
                        return net, end_points
                    net = slim.conv2d(net, 64, 1, scope='reduce8')
                    self.end_point_list.append('reduce8')
                    if add_and_check_final('reduce8', net):
                        return net, end_points
                    net = slim.conv2d(
                        net, 128, net.get_shape()[1:3],
                        padding='VALID', scope='fullconn8')
                    self.end_point_list.append('fullconn8')
                    if add_and_check_final('fullconn8', net):
                        return net, end_points
                    net = slim.dropout(
                        net, 0.5, scope='dropout8')
                    net = slim.flatten(net)
                    net = slim.fully_connected(
                        net, self.out_dim, scope='output8')
                    self.end_point_list.append(sc)
                    if add_and_check_final(sc, net):
                        return net, end_points

        raise ValueError('final_endpoint (%s) not recognized', final_endpoint)

        # batch_size = input_tensor.get_shape()[0].value
        # tf_util = import_module('utils.tf_util')
        # bn_decay = 0.9997
        # net = tf_util.conv2d(
        #     input_tensor, 16, [5, 5], stride=[1, 1], scope='conv1',
        #     padding='VALID', is_training=is_training, bn=True, bn_decay=bn_decay)
        # net = tf_util.max_pool2d(
        #     net, [4, 4], scope='maxpool1', padding='VALID')
        # net = tf_util.conv2d(
        #     net, 32, [3, 3], stride=[1, 1], scope='conv2',
        #     padding='VALID', is_training=is_training, bn=True, bn_decay=bn_decay)
        # net = tf_util.max_pool2d(
        #     net, [2, 2], scope='maxpool2', padding='VALID')
        # net = tf_util.conv2d(
        #     net, 64, [3, 3], stride=[1, 1], scope='conv3',
        #     padding='VALID', is_training=is_training, bn=True, bn_decay=bn_decay)
        # net = tf_util.max_pool2d(
        #     net, [2, 2], scope='maxpool3', padding='VALID')
        # # print(net.shape)
        #
        # net = tf.reshape(net, [self.batch_size, -1])
        # net = tf_util.fully_connected(
        #     net, 1024, scope='fullconn1',
        #     is_training=is_training, bn=True, bn_decay=bn_decay)
        # net = tf_util.dropout(
        #     net, keep_prob=0.5, scope='dropout1', is_training=is_training)
        # net = tf_util.fully_connected(
        #     net, self.out_dim, scope='fullconn3', activation_fn=None)

        return net, end_points

    def placeholder_inputs(self, batch_size=None):
        if batch_size is None:
            batch_size = self.batch_size
        frames_tf = tf.placeholder(
            tf.float32, shape=(
                batch_size,
                self.crop_size, self.crop_size,
                1))
        poses_tf = tf.placeholder(
            tf.float32, shape=(batch_size, self.out_dim))
        return frames_tf, poses_tf

    def get_loss(self, pred, anno, end_points):
        """ simple sum-of-squares loss
            pred: BxJ
            anno: BxJ
        """
        # loss = tf.reduce_sum(tf.pow(tf.subtract(pred, anno), 2)) / 2
        loss = tf.nn.l2_loss(pred - anno)  # already divided by 2
        # loss = tf.reduce_mean(tf.squared_difference(pred, anno)) / 2
        return loss
