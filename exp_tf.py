import numpy as np
import tensorflow as tf
import argparse
from sklearn import metrics
from tqdm import tqdm

from dataset import get_all_files
from music import *
from midi_util import *
from keras.layers.recurrent import GRU

NUM_NOTES = MAX_NOTE - MIN_NOTE
BATCH_SIZE = 32
TIME_STEPS = 32
model_file = 'out/saves/model'

class Model:
    def __init__(self, batch_size=BATCH_SIZE, time_steps=TIME_STEPS, training=True, dropout = 0.5):
        """
        Input
        """
        # Input note of the current time step
        note_in = tf.placeholder(tf.float32, [batch_size, time_steps, NUM_NOTES])
        # Target note to predict
        note_target = tf.placeholder(tf.float32, [batch_size, time_steps, NUM_NOTES])

        # Main output pathway
        out = note_in

        """
        Conv
        """
        with tf.variable_scope('conv1'):
            fil = tf.get_variable('W', [3, NUM_NOTES, NUM_NOTES])
            out = tf.nn.conv1d(out, fil, stride=1, padding='SAME')
            out = tf.nn.relu(out)
            out = tf.layers.dropout(inputs=out, rate=dropout, training=training)

        """
        Note invariant
        """
        state_size = 256

        """
        # Output of the same RNN for each note
        rnn_note_outs = []

        # Every single note connects to the same note invariant RNN
        cell = tf.contrib.rnn.GRUCell(state_size)
        # TODO: There are 48 RNN states that we've to keep track of?
        # Initial state of the memory.
        init_state = cell.zero_state(batch_size, tf.float32)

        for i in range(NUM_NOTES):
            with tf.variable_scope('rnn', reuse=i > 0):
                # There are a bunch of RNN states now...
                rnn_out, final_state = tf.nn.dynamic_rnn(cell, out[:, :, i:i+1], initial_state=init_state)
                rnn_out = tf.layers.dropout(inputs=rnn_out, rate=dropout, training=training)

            ### Sigmoid layer that predicts the one note only ###
            with tf.variable_scope('predict', reuse=i > 0):
                logits = tf.layers.dense(inputs=rnn_out, units=1)
                assert logits.get_shape()[0] == batch_size
                assert logits.get_shape()[1] == time_steps
                assert logits.get_shape()[2] == 1
            rnn_note_outs.append(logits)

        out = logits = tf.concat(rnn_note_outs, 2)
        assert out.get_shape()[0] == batch_size
        assert out.get_shape()[1] == time_steps
        assert out.get_shape()[2] == NUM_NOTES
        """

        ### Simple approach ###
        # TODO: Current performs better
        ### RNN Layer ###
        cell = tf.contrib.rnn.GRUCell(state_size)
        # Initial state of the memory.
        init_state = cell.zero_state(batch_size, tf.float32)
        rnn_out, final_state = tf.nn.dynamic_rnn(cell, note_in, initial_state=init_state)
        rnn_out = tf.layers.dropout(inputs=rnn_out, rate=dropout, training=training)

        ### Sigmoid Layer ###
        logits = tf.layers.dense(inputs=rnn_out, units=NUM_NOTES)

        """
        Consolidate logits into predictions
        """
        # Next note predictions
        self.prob = tf.nn.sigmoid(logits)
        # Classification prediction for f1 score
        self.pred = tf.round(self.prob)

        """
        Loss
        """
        total_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=logits, labels=note_target))
        train_step = tf.train.AdamOptimizer().minimize(total_loss)

        """
        Set instance vars
        """
        self.note_in = note_in
        self.note_target = note_target

        self.init_state = init_state
        self.final_state = final_state

        self.loss = total_loss
        self.train_step = train_step

        # Saver
        self.saver = tf.train.Saver()

    def train(self, sess, train_seqs, num_epochs=100, verbose=True):
        total_steps = 0

        for epoch in range(num_epochs):
            # Metrics
            training_loss = 0
            f1_score = 0
            step = 0

            # Bar
            t = tqdm(train_seqs)
            t.set_description('{}/{}'.format(epoch + 1, num_epochs))

            for seq in t:
                # Reset state
                t_state = None

                for X, Y in tqdm(seq):
                    feed_dict = { self.note_in: X, self.note_target: Y }

                    if t_state is not None:
                        feed_dict[self.init_state] = t_state

                    pred, t_loss, t_state, _ = sess.run([
                            self.pred,
                            self.loss,
                            self.final_state,
                            self.train_step
                        ],
                        feed_dict
                    )

                    training_loss += t_loss
                    step += 1
                    # Compute F-1 score of all timesteps and batches
                    # For every single sample in the batch
                    f1_score += np.mean([metrics.f1_score(y, p, average='weighted') for y, p in zip(Y, pred)])
                    t.set_postfix(loss=training_loss / step, f1=f1_score / step)

                    if total_steps % 1000 == 0:
                        # print('Saving at epoch {}'.format(epoch))
                        self.saver.save(sess, model_file)
                    total_steps += 1

    def generate(self, sess, inspiration, length=NOTES_PER_BAR * 16):
        # Resulting generation
        results = []
        # Current RNN state
        state = None
        # Current note
        current_note = np.zeros(NUM_NOTES)

        for i in range(length + len(inspiration)):
            if state is not None:
                # Not the first prediction
                feed_dict = { self.note_in: [[current_note]], self.init_state: state }
            else:
                # First prediction
                feed_dict = { self.note_in: [[current_note]] }

            prob, state = sess.run([self.prob, self.final_state], feed_dict)

            if i < len(inspiration):
                # Priming notes
                current_note = inspiration[i]
            else:
                prob = prob[0][0]
                # Randomly choose classes for each class
                current_note = np.zeros(NUM_NOTES)

                for n in range(NUM_NOTES):
                    current_note[n] = 1 if np.random.random() <= prob[n] else 0

                results.append(current_note)
        return results

def create_dataset(data, look_back):
    dataX, dataY = [], []

    # First note prediction
    data = [np.zeros_like(data[0])] + list(data)

    for i in range(len(data) - look_back - 1):
        dataX.append(data[i:(i + look_back)])
        dataY.append(data[i + 1:(i + look_back + 1)])
    return dataX, dataY

def chunk(a, size):
    trim_size = (len(a) // size) * size
    return np.swapaxes(np.split(np.array(a[:trim_size]), size), 0, 1)

def reset_graph():
    if 'sess' in globals() and sess:
        sess.close()
    tf.reset_default_graph()

parser = argparse.ArgumentParser(description='Generates music.')
parser.add_argument('--train', default=False, action='store_true', help='Train model?')
args = parser.parse_args()

print('Preparing training data')

# Load training data
dataset = ['data/classical/bach']#'data/classical/mozart'
sequences = [load_midi(f) for f in get_all_files(dataset)]
sequences = [np.minimum(np.ceil(m[:, MIN_NOTE:MAX_NOTE]), 1) for m in sequences]

train_seqs = []

for seq in sequences:
    train_data, label_data = create_dataset(seq, TIME_STEPS)

    # Chunk into batches
    train_data = chunk(train_data, BATCH_SIZE)
    label_data = chunk(label_data, BATCH_SIZE)
    train_seqs.append(list(zip(train_data, label_data)))

if args.train:
    with tf.Session() as sess:
        print('Training...')
        train_model = Model()
        sess.run(tf.global_variables_initializer())
        train_model.train(sess, train_seqs, 100)

reset_graph()

with tf.Session() as sess:
    print('Generating...')
    gen_model = Model(1, 1, training=False)
    gen_model.saver.restore(sess, model_file)

    for s in range(5):
        print('s={}'.format(s))
        composition = gen_model.generate(sess, np.random.choice(sequences)[:NOTES_PER_BAR])
        composition = np.concatenate((np.zeros((len(composition), MIN_NOTE)), composition), axis=1)
        midi.write_midifile('out/result_{}.mid'.format(s), midi_encode(composition))
