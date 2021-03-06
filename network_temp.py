# Shared Feature Extraction Layer
from __future__ import absolute_import
import os
import pickle
import sys
import json
import math
from keras.preprocessing.sequence import pad_sequences
import numpy as np
import keras.backend.tensorflow_backend as K
from keras.layers.core import Layer
from keras import initializers, regularizers, constraints
# from keras import backend as K
from keras.models import Model, Sequential
from keras.layers import Input, Layer, Lambda
from keras.layers import Dense, BatchNormalization
from keras.layers import Dropout
from keras.layers import Activation, RepeatVector, Reshape, Bidirectional, TimeDistributed
from keras.layers.recurrent import LSTM
from keras.layers.merge import concatenate, dot, subtract, maximum, multiply
from keras.layers import merge
from keras.activations import softmax
from keras import optimizers, metrics
from keras.callbacks import EarlyStopping
from keras.utils import Sequence

from keras.layers import InputSpec, Layer, Input, Dense, merge
from keras.layers import Lambda, Activation, Dropout, Embedding, TimeDistributed
from keras.layers import Bidirectional, GRU, LSTM
from keras.layers.noise import GaussianNoise
from keras.layers.advanced_activations import ELU
import keras.backend as K
from keras.models import Sequential, Model, model_from_json
from keras.regularizers import l2
from keras.optimizers import Adam
from keras.layers.normalization import BatchNormalization
from keras.layers.pooling import GlobalAveragePooling1D, GlobalMaxPooling1D
from keras.layers import Merge


# Some Macros
DEBUG = True
DATA_DIR = './data/training/pairwise'
EPOCHS = 250
BATCH_SIZE = 3000 # Around 11 splits for full training dataset
LEARNING_RATE = 0.001
LOSS = 'categorical_crossentropy'
NEGATIVE_SAMPLES = 100
OPTIMIZER = optimizers.Adam(LEARNING_RATE)

'''
    F1 measure functions
'''
def recall(y_true, y_pred):
    """Recall metric.
    Only computes a batch-wise average of recall.
    Computes the recall, a metric for multi-label classification of
    how many relevant items are selected.
    """
    true_positives = K.sum(K.round(K.clip(y_true * y_pred, 0, 1)))
    possible_positives = K.sum(K.round(K.clip(y_true, 0, 1)))
    recall = true_positives / (possible_positives + K.epsilon())
    return recall


def fbeta_score(y_true, y_pred, beta=1):
    """Computes the F score.
    The F score is the weighted harmonic mean of precision and recall.
    Here it is only computed as a batch-wise average, not globally.
    This is useful for multi-label classification, where input samples can be
    classified as sets of labels. By only using accuracy (precision) a model
    would achieve a perfect score by simply assigning every class to every
    input. In order to avoid this, a metric should penalize incorrect class
    assignments as well (recall). The F-beta score (ranged from 0.0 to 1.0)
    computes this, as a weighted mean of the proportion of correct class
    assignments vs. the proportion of incorrect class assignments.
    With beta = 1, this is equivalent to a F-measure. With beta < 1, assigning
    correct classes becomes more important, and with beta > 1 the metric is
    instead weighted towards penalizing incorrect class assignments.
    """
    if beta < 0:
        raise ValueError('The lowest choosable beta is zero (only precision).')

    # If there are no true positives, fix the F score at 0 like sklearn.
    if K.sum(K.round(K.clip(y_true, 0, 1))) == 0:
        return 0

    p = precision(y_true, y_pred)
    r = recall(y_true, y_pred)
    bb = beta ** 2
    fbeta_score = (1 + bb) * (p * r) / (bb * p + r + K.epsilon())
    return fbeta_score


def precision(y_true, y_pred):
    """Precision metric.
    Only computes a batch-wise average of precision.
    Computes the precision, a metric for multi-label classification of
    how many selected items are relevant.
    """
    true_positives = K.sum(K.round(K.clip(y_true * y_pred, 0, 1)))
    predicted_positives = K.sum(K.round(K.clip(y_pred, 0, 1)))
    precision = true_positives / (predicted_positives + K.epsilon())
    return true_positives


def true_positives(y_true, y_pred):
    return K.sum(K.round(K.clip(y_true * y_pred, 0, 1)))


def predicted_positives(y_true, y_pred):
    return K.sum(K.round(K.clip(y_pred, 0, 1)))


def fmeasure(y_true, y_pred):
    """Computes the f-measure, the harmonic mean of precision and recall.
    Here it is only computed as a batch-wise average, not globally.
    """
    return fbeta_score(y_true, y_pred, beta=1)


def smart_save_model(model):
    """
        Function to properly save the model to disk.
            If the model config is the same as one already on disk, overwrite it.
            Else make a new folder and write things there

    :return: None
    """

    # Get the model description
    desc = model.to_json()

    # Find the current model dirs in the data dir.
    _, dirs, _ = os.walk(DATA_DIR).next()

    # If no folder found in there, create a new one.
    if len(dirs) == 0:
        os.mkdir(os.path.join(DATA_DIR, "model_00"))
        dirs = ["model_00"]

    # Find the latest folder in here
    dir_nums = sorted([ x[-2:] for x in dirs])
    l_dir = os.path.join(DATA_DIR, "model_" + dir_nums[-1])

    # Check if the latest dir has the same model as current
    try:
        if json.load(open(os.path.join(l_dir, 'model.json'))) == desc:
            # Same desc. Just save stuff here
            if DEBUG:
                print "network.py:smart_save_model: Saving model in %s" % l_dir
            model.save(os.path.join(l_dir, 'model.h5'))

        else:
            # Diff model. Make new folder and do stuff. @TODO this
            new_num = int(dir_nums[-1]) + 1
            if new_num < 10:
                new_num = str('0') + str(new_num)
            else:
                new_num = str(new_num)

            l_dir = os.path.join(DATA_DIR, "model_" + new_num)
            os.mkdir(l_dir)
            raise IOError

    except IOError:

        # Apparently there's nothing here. Let's set camp.
        if DEBUG:
            print "network.py:smart_save_model: Saving model in %s" % l_dir
        model.save(os.path.join(l_dir, 'model.h5'))
        json.dump(desc, open(os.path.join(l_dir, 'model.json'), 'w+'))

def zeroloss(yt, yp):
    return 0.0

def custom_loss(y_true, y_pred):
    '''
        max margin loss
    '''
    # y_pos = y_pred[0]
    # y_neg= y_pred[1]
    diff = y_pred[:,-1]
    # return K.sum(K.maximum(1.0 - diff, 0.))
    return K.sum(diff)


def rank_precision(model, test_questions, test_pos_paths, test_neg_paths):
    only_questions = test_questions[range(0, test_questions.shape[0], NEGATIVE_SAMPLES)]
    only_pos_paths = test_pos_paths[range(0, test_pos_paths.shape[0], NEGATIVE_SAMPLES)]

    pos_outputs = model.predict([only_questions, only_pos_paths, only_pos_paths])[:,0]
    pos_outputs = np.reshape(pos_outputs, [only_pos_paths.shape[0], 1])
    neg_outputs = model.predict([test_questions, test_neg_paths, test_neg_paths])[:,0]
    neg_outputs = np.reshape(neg_outputs, [only_pos_paths.shape[0], NEGATIVE_SAMPLES])
    all_outputs = np.hstack([pos_outputs, neg_outputs])

    precision = float(len(np.where(np.argmax(all_outputs, axis=1)==0)[0]))/all_outputs.shape[0]
    return precision


class IdBasedDataGenerator(Sequence):
    def __init__(self, questions, pos_paths, neg_paths, max_length, neg_paths_per_epoch, batch_size):
        self.dummy_y = np.zeros(batch_size)
        self.firstDone = False
        self.max_length = max_length
        self.neg_paths_per_epoch = neg_paths_per_epoch
        self.questions = np.repeat(questions, self.neg_paths_per_epoch, axis=0)
        self.pos_paths = np.repeat(pos_paths, self.neg_paths_per_epoch, axis=0)
        self.neg_paths = neg_paths
        self.batch_size = batch_size

    def __len__(self):
        return math.ceil(len(self.questions)*self.neg_paths_per_epoch / self.batch_size)

    def __getitem__(self, idx):
        index = lambda x: x[idx * self.batch_size:(idx + 1) * self.batch_size]
        batch_questions = index(self.questions)
        batch_pos_paths = index(self.pos_paths)
        batch_neg_paths = index(np.reshape(self.neg_paths[:, np.random.randint(0, NEGATIVE_SAMPLES, self.neg_paths_per_epoch), :], (-1, self.max_length)))

        # if self.firstDone == False:
        #     batch_neg_paths = index(self.neg_paths)
        # else:
        #     batch_neg_paths = neg_paths[np.random.randint(0, neg_paths.shape[0], BATCH_SIZE)]


        return ([batch_questions, batch_pos_paths, batch_neg_paths], self.dummy_y)

    def on_epoch_end(self):
        self.firstDone = not self.firstDone

class DataGenerator(Sequence):
    def __init__(self, questions, pos_paths, neg_paths, batch_size):
        self.dummy_y = np.zeros(BATCH_SIZE)
        self.firstDone = False
        self.questions, self.pos_paths, self.neg_paths = questions, pos_paths, neg_paths
        self.batch_size = batch_size

    def __len__(self):
        return math.ceil(len(self.questions) / self.batch_size)

    def __getitem__(self, idx):
        index = lambda x: x[idx * self.batch_size:(idx + 1) * self.batch_size]
        batch_questions = index(self.questions)
        batch_pos_paths = index(self.pos_paths)

        if self.firstDone == False:
            batch_neg_paths = index(self.neg_paths)
        else:
            batch_neg_paths = neg_paths[np.random.randint(0, neg_paths.shape[0], BATCH_SIZE)]

        return ([batch_questions, batch_pos_paths, batch_neg_paths], self.dummy_y)

    def on_epoch_end(self):
        self.firstDone = not self.firstDone

def rank_precision_metric(neg_paths_per_epoch):
    def metric(y_true, y_pred):
        pos_outputs, neg_outputs = y_pred[:,0], y_pred[:,1]
        pos_outputs = K.gather(pos_outputs, K.arange(0, K.shape(y_pred)[0], neg_paths_per_epoch))
        neg_outputs = K.reshape(neg_outputs, [K.shape(pos_outputs)[0], neg_paths_per_epoch])
        all_outputs = K.concatenate([K.reshape(pos_outputs, (-1,1)), neg_outputs], axis=1)
        hits = K.cast(K.shape(K.tf.where(K.tf.equal(K.tf.argmax(all_outputs, axis=1),0)))[0], 'float32')
        precision = hits/K.cast(K.shape(all_outputs)[0], 'float32')
        # precision = float(len(np.where(np.argmax(all_outputs, axis=1)==0)[0]))/all_outputs.shape[0]
        return precision
    return metric

class _Attention(object):
    def __init__(self, max_length, nr_hidden, dropout=0.0, L2=0.0, activation='relu'):
        self.max_length = max_length
        self.model = Sequential()
        self.model.add(Dropout(dropout, input_shape=(nr_hidden,)))
        self.model.add(
            Dense(nr_hidden, name='attend1',
                init='he_normal', W_regularizer=l2(L2),
                input_shape=(nr_hidden,), activation='relu'))
        self.model.add(Dropout(dropout))
        self.model.add(Dense(nr_hidden, name='attend2',
            init='he_normal', W_regularizer=l2(L2), activation='relu'))
        self.model = TimeDistributed(self.model)

    def __call__(self, sent1, sent2):
        def _outer(AB):
            att_ji = K.batch_dot(AB[1], K.permute_dimensions(AB[0], (0, 2, 1)))
            return K.permute_dimensions(att_ji,(0, 2, 1))
        return merge(
                [self.model(sent1), self.model(sent2)],
                mode=_outer,
                output_shape=(self.max_length, self.max_length))


class _SoftAlignment(object):
    def __init__(self, max_length, nr_hidden):
        self.max_length = max_length
        self.nr_hidden = nr_hidden

    def __call__(self, sentence, attention, transpose=False):
        def _normalize_attention(attmat):
            att = attmat[0]
            mat = attmat[1]
            if transpose:
                att = K.permute_dimensions(att,(0, 2, 1))
            # 3d softmax
            e = K.exp(att - K.max(att, axis=-1, keepdims=True))
            s = K.sum(e, axis=-1, keepdims=True)
            sm_att = e / s
            return K.batch_dot(sm_att, mat)
        return merge([attention, sentence], mode=_normalize_attention,
                      output_shape=(self.max_length, self.nr_hidden)) # Shape: (i, n)


class _Comparison(object):
    def __init__(self, words, nr_hidden, L2=0.0, dropout=0.0):
        self.words = words
        self.model = Sequential()
        self.model.add(Dropout(dropout, input_shape=(nr_hidden*2,)))
        self.model.add(Dense(nr_hidden, name='compare1',
            init='he_normal', W_regularizer=l2(L2)))
        self.model.add(Activation('relu'))
        self.model.add(Dropout(dropout))
        self.model.add(Dense(nr_hidden, name='compare2',
                        W_regularizer=l2(L2), init='he_normal'))
        self.model.add(Activation('relu'))
        self.model = TimeDistributed(self.model)

    def __call__(self, sent, align, **kwargs):
        result = self.model(merge([sent, align], mode='concat')) # Shape: (i, n)
        avged = GlobalAveragePooling1D()(result)
        maxed = GlobalMaxPooling1D()(result)
        merged = merge([avged, maxed])
        result = BatchNormalization()(merged)
        return result


class _Entailment(object):
    def __init__(self, nr_hidden, nr_out, dropout=0.0, L2=0.0):
        self.model = Sequential()
        self.model.add(Dropout(dropout, input_shape=(nr_hidden*2,)))
        self.model.add(Dense(nr_hidden, name='entail1',
            init='he_normal', W_regularizer=l2(L2)))
        self.model.add(Activation('relu'))
        self.model.add(Dropout(dropout))
        self.model.add(Dense(nr_hidden, name='entail2',
            init='he_normal', W_regularizer=l2(L2)))
        self.model.add(Activation('relu'))
        # self.model.add(Dense(nr_out, name='entail_out', activation='softmax',
        #                 W_regularizer=l2(L2), init='zero'))

    def __call__(self, feats1, feats2):
        features = merge([feats1, feats2], mode='concat')
        return self.model(features)

class _GlobalSumPooling1D(Layer):
    '''Global sum pooling operation for temporal data.
    # Input shape
        3D tensor with shape: `(samples, steps, features)`.
    # Output shape
        2D tensor with shape: `(samples, features)`.
    '''
    def __init__(self, **kwargs):
        super(_GlobalSumPooling1D, self).__init__(**kwargs)
        self.input_spec = [InputSpec(ndim=3)]

    def get_output_shape_for(self, input_shape):
        return (input_shape[0], input_shape[2])

    def call(self, x, mask=None):
        if mask is not None:
            return K.sum(x * K.clip(mask, 0, 1), axis=1)
        else:
            return K.sum(x, axis=1)

class _BiRNNEncoding(object):
    def __init__(self, max_length, embedding_dims, units, dropout=0.0):
        self.model = Sequential()
        self.model.add(Bidirectional(LSTM(units, return_sequences=True,
                                         dropout_W=dropout, dropout_U=dropout),
                                         input_shape=(max_length, embedding_dims)))
        self.model.add(TimeDistributed(Dense(units, activation='relu', init='he_normal')))
        self.model.add(TimeDistributed(Dropout(0.2)))

    def __call__(self, sentence):
        return self.model(sentence)

class _StaticEmbedding(object):
    def __init__(self, vectors, max_length, nr_out):
        self.nr_out = nr_out
        self.max_length = max_length
        self.embed = Embedding(
                        vectors.shape[0],
                        vectors.shape[1],
                        input_length=max_length,
                        weights=[vectors],
                        name='embed',
                        trainable=False)

        # self.project = TimeDistributed(
        #                     Dense(
        #                         nr_out,
        #                         activation=None,
        #                         bias=False,
        #                         name='project'))

    def __call__(self, sentence):
        return self.embed(sentence)

def get_glove_embeddings():
    from utils.embeddings_interface import __prepare__
    __prepare__(_word2vec=False, _glove=True)

    from utils.embeddings_interface import glove_embeddings
    return glove_embeddings

def main():

    gpu = sys.argv[1]
    os.environ['CUDA_VISIBLE_DEVICES'] = gpu


    """
        Data Time!
    """
    # Pull the data up from disk
    max_length = 50
    with open(DATA_DIR + "/data_embedded_phase_i.pickle") as fp:
        dataset = pickle.load(fp)
    questions = [i[0] for i in dataset]
    questions = pad_sequences(questions, maxlen=max_length, padding='post')
    pos_paths = [i[1] for i in dataset]
    pos_paths = pad_sequences(pos_paths, maxlen=max_length, padding='post')
    neg_paths = [i[2] for i in dataset]
    neg_paths = [path for paths in neg_paths for path in paths]
    neg_paths = pad_sequences(neg_paths, maxlen=max_length, padding='post')
    neg_paths = np.reshape(neg_paths, (len(questions), NEGATIVE_SAMPLES, max_length))
    # pad_till = abs(pos_paths.shape[1] - questions.shape[1])
    # pad = lambda x: np.pad(x, [(0,0), (0,pad_till), (0,0)], 'constant', constant_values=0.)
    # if pos_paths.shape[1] < questions.shape[1]:
    #     pos_paths = pad(pos_paths)
    #     neg_paths = pad(neg_paths)
    # else:
    #     questions = pad(questions)

    # Shuffle these matrices together @TODO this!
    np.random.seed(0) # Random train/test splits stay the same between runs

    # Divide the data into diff blocks
    split_point = lambda x: int(len(x) * .80)

    def train_split(x):
        return x[:split_point(x)]
    def test_split(x):
        return x[split_point(x):]

    train_pos_paths = train_split(pos_paths)
    train_neg_paths = train_split(neg_paths)
    train_questions = train_split(questions)

    test_pos_paths = test_split(pos_paths)
    test_neg_paths = test_split(neg_paths)
    test_questions = test_split(questions)

    neg_paths_per_epoch = 20
    dummy_y_train = np.zeros(len(train_questions))
    dummy_y_test = np.zeros(len(test_questions)*neg_paths_per_epoch)

    print train_questions.shape
    print train_pos_paths.shape
    print train_neg_paths.shape

    with K.tf.device('/gpu:' + gpu):
        K.set_session(K.tf.Session(config=K.tf.ConfigProto(allow_soft_placement=True)))
        """
            Model Time!
        """
        max_length = train_questions.shape[1]
        # Define input to the models
        x_ques = Input(shape=(max_length,), dtype='int32', name='x_ques')
        x_pos_path = Input(shape=(max_length,), dtype='int32', name='x_pos_path')
        x_neg_path = Input(shape=(max_length,), dtype='int32', name='x_neg_path')

        vectors = get_glove_embeddings()
        neg_paths_per_epoch = 20
        embedding_dims = vectors.shape[1]
        nr_hidden = 64

        embed = _StaticEmbedding(vectors, max_length, embedding_dims)
        encode = _BiRNNEncoding(max_length, embedding_dims,  nr_hidden, 0.5)


        x_ques_embedded = embed(x_ques)
        x_pos_path_embedded = embed(x_pos_path)
        x_neg_path_embedded = embed(x_neg_path)

        ques_encoded = encode(x_ques_embedded)
        x_pos_path_encoded = encode(x_pos_path_embedded)
        x_neg_path_encoded = encode(x_neg_path_embedded)

        def getScore(path_encoded):
            return dot([ques_encoded, path_encoded], axes=-1)

        pos_score = getScore(x_pos_path_encoded)
        neg_score = getScore(x_neg_path_encoded)

        loss = Lambda(lambda x: K.maximum(0., 1.0 - x[0] + x[1]))([pos_score, neg_score])

        output = concatenate([pos_score, neg_score, loss], axis=-1)

        # Model time!
        model = Model(inputs=[x_ques, x_pos_path, x_neg_path],
            outputs=[output])

        print(model.summary())

        model.compile(optimizer=OPTIMIZER,
                      loss=custom_loss, metrics=[rank_precision_metric(neg_paths_per_epoch)])

        # Prepare training data
        training_input = [train_questions, train_pos_paths, train_neg_paths]

        def validation_generator():
            questions = np.repeat(test_questions, neg_paths_per_epoch, axis=0)
            pos_paths = np.repeat(test_pos_paths, neg_paths_per_epoch, axis=0)
            while True:
                neg_paths = np.reshape(test_neg_paths[:, np.random.randint(0, NEGATIVE_SAMPLES, neg_paths_per_epoch), :], (-1, max_length))
                yield (questions, pos_paths, neg_paths), dummy_y_test


        model.fit_generator(IdBasedDataGenerator(train_questions, train_pos_paths, train_neg_paths, 50, neg_paths_per_epoch, BATCH_SIZE), epochs=EPOCHS,
            validation_data=validation_generator())
            # callbacks=[EarlyStopping(monitor='val_loss', min_delta=0, patience=0, verbose=0, mode='auto')
    # ])

        smart_save_model(model)

        # Prepare test data

        print "Precision (hits@1) = ", rank_precision(model, test_questions, test_pos_paths, test_neg_paths)

    # print "Evaluation Complete"
    # print "Loss     = ", results[0]
    # print "F1 Score = ", results[1]
    # print "Accuracy = ", results[2]




if __name__ == "__main__":
    main()x`x`