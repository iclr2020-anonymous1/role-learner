# Functions needed for training models

from __future__ import unicode_literals, print_function, division
from io import open
import unicodedata
import string
import re
import random
from random import shuffle

import torch
import torch.nn as nn
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F

import numpy as np

import sys
import os

import time
import math

import pickle

from role_assignment_functions import *
from evaluation import *
from rolelearner.role_learning_tensor_product_encoder import RoleLearningTensorProductEncoder

use_cuda = torch.cuda.is_available()

# Train for a single batch
# Inputs: 
#   training_set: the batch
#   encoder: the encoder
#   decoder: the decoder
#   encoder_optimizer: optimizer for the encoder
#   decoder_optimizer: optimizer for the decoder
#   criterion: the loss function
#   input_to_output: function that maps input sequences to correct outputs
def train(training_set, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion, input_to_output):
    loss = 0

    # Get the decoder's outputs outputs for these inputs
    logits = decoder(encoder(training_set),
                     len(training_set[0]),
                     [parse_digits(elt) for elt in training_set])

    encoder_optimizer.zero_grad()
    decoder_optimizer.zero_grad()

    # Compute the loss over each index in the output
    for index, logit in enumerate(logits):
        if use_cuda:
            loss += criterion(logit, Variable(torch.LongTensor([[input_to_output(x) for x in training_set]])).cuda().transpose(0,2)[index].view(-1))
        else:
            loss += criterion(logit, Variable(torch.LongTensor([[input_to_output(x) for x in training_set]])).transpose(0,2)[index].view(-1))

    # Backpropagate the loss
    loss.backward()
    encoder_optimizer.step()
    decoder_optimizer.step()

    return loss / len(training_set)

def train_mix(training_set, encoder, decoder, tpr_encoder, role_function, decoder_optimizer, tpr_optimizer, criterion, input_to_output):
    loss = 0

    decoder_optimizer.zero_grad()
    tpr_optimizer.zero_grad()

    tpr_batch_f = Variable(torch.LongTensor(training_set))
    tpr_batch_r = Variable(torch.LongTensor([role_function(x) for x in training_set]))

    if use_cuda:
        tpr_batch_f = tpr_batch_f.cuda()
        tpr_batch_r = tpr_batch_r.cuda()

    tpr_encoding = tpr_encoder(tpr_batch_f, tpr_batch_r)
    #print(tpr_encoding.size())
    logits_tpr = decoder(tpr_encoding, len(training_set[0]), [parse_digits(elt) for elt in training_set])


    for index, logit in enumerate(logits_tpr):
        if use_cuda:
            loss += criterion(logit, Variable(torch.LongTensor([[input_to_output(x) for x in training_set]])).cuda().transpose(0,2)[index].view(-1))
        else:
            loss += criterion(logit, Variable(torch.LongTensor([[input_to_output(x) for x in training_set]])).transpose(0,2)[index].view(-1))


    loss /= len(training_set)
    #loss += 0.01*criterion_tpr(encoding, tpr_encoding)

    # Backpropagate the loss
    loss.backward()
    decoder_optimizer.step()
    tpr_optimizer.step()

    return loss, tpr_encoding

def train_iters_mix(
        encoder,
        decoder,
        tpr_encoder,
        role_function,
        train_data,
        dev_data,
        file_prefix,
        input_to_output,
        encoder_file,
        decoder_file,
        max_epochs=100,
        patience=1,
        print_every=1000,
        learning_rate=0.001,
        batch_size=32,
        output_dir=None):
    print_loss_total = 0

    # Train using Adam
    decoder_optimizer = optim.Adam(decoder.parameters(), lr=learning_rate)
    tpr_optimizer= optim.Adam(tpr_encoder.parameters(), lr=learning_rate)

    # Negative log likelihood loss
    criterion = nn.NLLLoss()
    best_loss = 1000000
    epochs_since_improved = 0

    # Group the data into batches
    training_sets = batchify(train_data, batch_size)
    dev_data = batchify(dev_data, batch_size)
    loss_total = 0

    # File for printing updates
    if output_dir:
        progress_file = open(os.path.join(output_dir, "progress_" + file_prefix), "w")
    else:
        progress_file = open("models/progress_" + file_prefix, "w")

    # Iterate over epocjs
    for epoch in range(max_epochs):
        improved_this_epoch = 0
        shuffle(training_sets)

        # Iterate over batches
        for batch, training_set in enumerate(training_sets):

            # Train for this batch
            loss, encoding = train_mix(training_set, encoder, decoder, tpr_encoder, role_function, decoder_optimizer, tpr_optimizer, criterion, input_to_output)

            # Print an update and save the weights every print_every iterations
            if batch % print_every == 0:
                this_loss = dev_loss_mix(tpr_encoder, role_function, decoder, criterion, dev_data, input_to_output)
                progress_file.write(str(epoch) + "\t" + str(batch) + "\t" + str(this_loss.item()) + "\n")
                print(this_loss.data.item())
                if this_loss.data.item() < best_loss:
                    improved_this_epoch = 1
                    best_loss = this_loss.data.item()

                    torch.save(tpr_encoder.state_dict(), encoder_file)
                    torch.save(decoder.state_dict(), decoder_file)

        # Early stopping
        if not improved_this_epoch:
            epochs_since_improved += 1
            if epochs_since_improved == patience:
                break

        else:
            epochs_since_improved = 0


# Compute the loss on the development set
# Inputs:
#    encoder: the encoder
#    decoder: the decoder
#    criterion: the loss function
#    dev_set: the development set
#    input_to_output: function that maps input sequences to correct outputs
def dev_loss(encoder, decoder, criterion, dev_set, input_to_output):
    dev_loss_val = 0

    for dev_elt in dev_set:
        logits = decoder(encoder(dev_elt),
                       len(dev_elt[0]),
                         [parse_digits(elt) for elt in dev_elt])

        for index, logit in enumerate(logits):
                    if use_cuda:
                        dev_loss_val += criterion(logit, Variable(torch.LongTensor([[input_to_output(x) for x in dev_elt]])).cuda().transpose(0,2)[index].view(-1))
                    else:
                        dev_loss_val += criterion(logit, Variable(torch.LongTensor([[input_to_output(x) for x in dev_elt]])).transpose(0,2)[index].view(-1))

    return dev_loss_val / len(dev_set)

# Compute the loss on the development set
# Inputs:
#    encoder: the encoder
#    decoder: the decoder
#    criterion: the loss function
#    dev_set: the development set
#    input_to_output: function that maps input sequences to correct outputs
def dev_loss_mix(tpr_encoder, role_function, decoder, criterion, dev_set, input_to_output):
    dev_loss_val = 0

    for dev_elt in dev_set:

        tpr_batch_f = Variable(torch.LongTensor(dev_elt))
        tpr_batch_r = Variable(torch.LongTensor([role_function(x) for x in dev_elt]))

        if use_cuda:
            tpr_batch_f = tpr_batch_f.cuda()
            tpr_batch_r = tpr_batch_r.cuda()

        tpr_encoding = tpr_encoder(tpr_batch_f, tpr_batch_r)
        #print(tpr_encoding.size())
        logits_tpr = decoder(tpr_encoding, len(dev_elt[0]), [parse_digits(elt) for elt in dev_elt])

        for index, logit in enumerate(logits_tpr):
                    if use_cuda:
                        dev_loss_val += criterion(logit, Variable(torch.LongTensor([[input_to_output(x) for x in dev_elt]])).cuda().transpose(0,2)[index].view(-1))
                    else:
                        dev_loss_val += criterion(logit, Variable(torch.LongTensor([[input_to_output(x) for x in dev_elt]])).transpose(0,2)[index].view(-1))

    return dev_loss_val / len(dev_set)


# Generate batches from a data set
def batchify(data, batch_size):
    length_sorted_dict = {}
    max_length = 0

    for item in data:
        if len(item) not in length_sorted_dict:
            length_sorted_dict[len(item)] = []
        length_sorted_dict[len(item)].append(item)
        if len(item) > max_length:
            max_length = len(item)

    batches = []

    for seq_len in range(max_length + 1):
        if seq_len in length_sorted_dict:
            for batch_num in range(len(length_sorted_dict[seq_len])//batch_size):
                this_batch = length_sorted_dict[seq_len][batch_num*batch_size:(batch_num+1)*batch_size]
                batches.append(this_batch)

    shuffle(batches)
    return batches


# Generate batches suitable for a TPDN from some dataset
def batchify_tpr(data, batch_size):
    length_sorted_dict = {}
    max_length = 0

    for item in data:
        if len(item[0]) not in length_sorted_dict:
            length_sorted_dict[len(item[0])] = []
        length_sorted_dict[len(item[0])].append(item)
        if len(item[0]) > max_length:
            max_length = len(item[0])

    batches = []

    for seq_len in range(max_length + 1):
        if seq_len in length_sorted_dict:
            for batch_num in range(len(length_sorted_dict[seq_len])//batch_size):
                this_batch = length_sorted_dict[seq_len][batch_num*batch_size:(batch_num+1)*batch_size]
                batches.append(this_batch)

    shuffle(batches)
    return batches


# Perform a full training run for a digit
# sequence task. Inputs:
#    encoder: the encoder
#    decoder: the decoder
#    train_data: the training set
#    dev_data: the development set
#    file_prefix: file identifier to use when saving the weights
#    input_to_output: function for mapping input sequences to the correct outputs
#    max_epochs: maximum number of epochs to train for before halting
#    patience: maximum number of epochs to train without dev set improvement before halting
#    print_every: number of batches to go through before printing the current status
#    learning_rate: learning rate
#    batch_size: batch_size
def train_iters(encoder, decoder, train_data, dev_data, file_prefix, input_to_output, max_epochs=100, patience=1, print_every=1000, learning_rate=0.001, batch_size=32):
    print_loss_total = 0

    # Train using Adam
    encoder_optimizer = optim.Adam(encoder.parameters(), lr=learning_rate)
    decoder_optimizer = optim.Adam(decoder.parameters(), lr=learning_rate)

    # Negative log likelihood loss
    criterion = nn.NLLLoss()
    best_loss = 1000000
    epochs_since_improved = 0

    # Group the data into batches
    training_sets = batchify(train_data, batch_size)
    dev_data = batchify(dev_data, batch_size)
    loss_total = 0

    # File for printing updates
    progress_file = open("models/progress_" + file_prefix, "w")

    # Iterate over epocjs
    for epoch in range(max_epochs):
        improved_this_epoch = 0
        shuffle(training_sets)

        # Iterate over batches
        for batch, training_set in enumerate(training_sets):

            # Train for this batch
            loss = train(training_set, encoder, decoder, encoder_optimizer, decoder_optimizer, criterion, input_to_output)
            # Print an update and save the weights every print_every iterations
            if batch % print_every == 0:
                this_loss = dev_loss(encoder, decoder, criterion, dev_data, input_to_output)
                progress_file.write(str(epoch) + "\t" + str(batch) + "\t" + str(this_loss.item()) + "\n")
                if this_loss.data[0] < best_loss:
                    improved_this_epoch = 1
                    best_loss = this_loss
                    torch.save(encoder.state_dict(), "models/encoder_" + file_prefix + ".weights")
                    torch.save(decoder.state_dict(), "models/decoder_" + file_prefix + ".weights")

        # Early stopping
        if not improved_this_epoch:
            epochs_since_improved += 1
            if epochs_since_improved == patience:
                print("Patience exceeded, finish training early")
                break

        else:
            epochs_since_improved = 0



# Training a TPDN for a single batch
def train_tpr(batch, tpr_encoder, tpr_optimizer, criterion, one_hot_temperature = 1.0):
    # Zero the gradient 
    tpr_optimizer.zero_grad()

    one_hot_loss = 0
    unique_role_loss = 0
    mse_loss = 0
    l2_norm_loss = 0

    # Iterate over this batch
    input_fillers = batch[0]  # The list of fillers for the input
    input_roles = batch[1]  # The list of roles hypothesized for the input
    target_variable = batch[2]  # The mystery vector associated with this input
    if use_cuda:
        input_fillers = input_fillers.cuda()
        input_roles = input_roles.cuda()
        target_variable = target_variable.cuda()

    if isinstance(tpr_encoder, RoleLearningTensorProductEncoder):
        tpr_encoder_output, role_predictions = tpr_encoder(input_fillers, input_roles)
        batch_one_hot_loss, batch_l2_loss, batch_unique_loss = \
            tpr_encoder.get_regularization_loss(role_predictions)
        one_hot_loss += batch_one_hot_loss
        l2_norm_loss += batch_l2_loss
        unique_role_loss += batch_unique_loss
    else:
        # Find the output for this input
        tpr_encoder_output = tpr_encoder(input_fillers, input_roles)

    # Find the loss associated with this output
    # loss += criterion(tpr_encoder_output.unsqueeze(0), target_variable)

    mse_loss += criterion(tpr_encoder_output, target_variable)

    loss = mse_loss + one_hot_loss + unique_role_loss + l2_norm_loss

    # Backpropagate the loss
    loss.backward()
    tpr_optimizer.step()

    # Return the loss
    return loss.data.item(), mse_loss, one_hot_loss, unique_role_loss, l2_norm_loss


# Training a TPDN for multiple iterations
def trainIters_tpr(train_data, dev_data, tpr_encoder, n_epochs,
                   learning_rate=0.001, batch_size=5, weight_file=None, patience=3,
                   use_one_hot_temperature=False, burn_in=0):
    # The optimization algorithm; could use SGD instead of Adam
    tpr_optimizer = optim.Adam(tpr_encoder.parameters(), lr=learning_rate)

    # Using mean squared error as the loss
    criterion = nn.MSELoss()
    prev_loss = 1000000
    # Keeps track of the number of epochs since improvement for early stopping
    count_epochs_not_improved = 0
    count_unhelpful_cuts = 0
    training_done = 0
    best_loss = prev_loss

    one_hot_temperature = 1.0
    if use_one_hot_temperature:
        one_hot_temperature = 0.0

    # Format the data
    train_data = batchify_tpr(train_data, batch_size)
    dev_data = batchify_tpr(dev_data, batch_size)

    training_sets = [(Variable(torch.LongTensor([item[0] for item in batch])),
                     Variable(torch.LongTensor([item[1] for item in batch])),
                     torch.cat([item[2].unsqueeze(0).unsqueeze(0) for item in batch], 1)) for batch in train_data]

    dev_data_sets = [(Variable(torch.LongTensor([item[0] for item in batch])),
                     Variable(torch.LongTensor([item[1] for item in batch])),
                     torch.cat([item[2].unsqueeze(0).unsqueeze(0) for item in batch], 1)) for batch in dev_data]

    reached_max_temp = False
    # Conduct the desired number of training examples
    for epoch in range(n_epochs):
        if burn_in == epoch:
            print('Burn in is over, turning on regularization')
            if isinstance(tpr_encoder, RoleLearningTensorProductEncoder):
                tpr_encoder.use_regularization(True)
            if burn_in == 0:
                print('Setting regularization temp to {}'.format(1))
                if isinstance(tpr_encoder, RoleLearningTensorProductEncoder):
                    tpr_encoder.set_regularization_temp(1)
                reached_max_temp = True

        if epoch >= burn_in and not reached_max_temp:
            temp = float(epoch - burn_in + 1) / burn_in
            if temp <= 1:
                print('Setting regularization temp to {}'.format(temp))
                tpr_encoder.set_regularization_temp(temp)
            else:
                reached_max_temp = True

        epoch_mse_loss = 0
        epoch_one_hot_loss = 0
        epoch_unique_role_loss = 0
        epoch_l2_norm_loss = 0

        shuffle(training_sets)

        if isinstance(tpr_encoder, RoleLearningTensorProductEncoder):
            tpr_encoder.train()

        for batch in training_sets:
            loss, batch_mse_loss, batch_one_hot_loss, batch_unique_role_loss, batch_l2_norm_loss = \
                train_tpr(batch, tpr_encoder, tpr_optimizer, criterion, one_hot_temperature)
            epoch_mse_loss += batch_mse_loss
            epoch_one_hot_loss += batch_one_hot_loss
            epoch_unique_role_loss += batch_unique_role_loss
            epoch_l2_norm_loss += batch_l2_norm_loss

        # Validate after the epoch
        val_mse = 0
        val_one_hot_loss = 0
        val_l2_loss = 0
        val_unique_role_loss = 0

        #if isinstance(tpr_encoder, RoleLearningTensorProductEncoder):
        #    tpr_encoder.eval()

        num_elements = 1 # Start at 1 to avoid division by 0
        num_elements_role_low = 0
        roles_predicted = []
        for i in range(len(dev_data_sets)):
            input_fillers = dev_data_sets[i][0]
            input_roles = dev_data_sets[i][1]
            target_variable = dev_data_sets[i][2]
            if use_cuda:
                input_fillers = input_fillers.cuda()
                input_roles = input_roles.cuda()
                target_variable = target_variable.cuda()
            if isinstance(tpr_encoder, RoleLearningTensorProductEncoder):
                out, role_predictions = tpr_encoder(input_fillers, input_roles)
                out = out.data
                batch_one_hot_loss, batch_l2_norm_loss, batch_unique_role_loss = \
                    tpr_encoder.get_regularization_loss(role_predictions)
                val_one_hot_loss += batch_one_hot_loss
                val_l2_loss += batch_l2_norm_loss
                val_unique_role_loss += batch_unique_role_loss

                for sequence_index in range(len(role_predictions)):
                    for batch_index in range(len(role_predictions[sequence_index])):
                        role_prediction = torch.argmax(role_predictions[sequence_index][batch_index])
                        if role_predictions[sequence_index][batch_index][role_prediction] < .98:
                            num_elements_role_low += 1
                        num_elements += 1
                        roles_predicted.append(role_prediction)
            else:
                out = tpr_encoder(input_fillers, input_roles).data
            val_mse += torch.mean(torch.pow(out - target_variable.data, 2))
        val_mse = val_mse / len(dev_data_sets)
        val_one_hot_loss = val_one_hot_loss / len(dev_data_sets)
        val_l2_loss = val_l2_loss / len(dev_data_sets)
        val_unique_role_loss = val_unique_role_loss / len(dev_data_sets)

        total_val_loss = val_mse + val_one_hot_loss + val_l2_loss + val_unique_role_loss
        print('Epoch {}\tvalidation loss: {}'.format(epoch, total_val_loss.item()))
        print('Val MSE loss: {}'.format(val_mse))
        print('Val one hot loss: {}'.format(val_one_hot_loss))
        print('Val unique role loss: {}'.format(val_unique_role_loss))
        print('Val l2 norm loss: {}'.format(val_l2_loss))
        print('num elements {}'.format(num_elements))
        print('number of roles used: {}'.format(len(np.unique(roles_predicted))))
        print('percentage low role prediction: {}'.format(
            100 * num_elements_role_low / num_elements))
        print('')
        #print('Training MSE loss: {}'.format(epoch_mse_loss))
        #print('Training one hot loss: {}'.format(epoch_one_hot_loss))
        #print('Training unique role loss: {}'.format(epoch_unique_role_loss))
        #print('Training l2 norm loss: {}'.format(epoch_l2_norm_loss))
        # When we turn on regularization, we want to start validating from the newest checkpoint
        if reached_max_temp or burn_in == epoch:
            if total_val_loss < best_loss:
                print('Saving model at epoch {}'.format(epoch))
                count_epochs_not_improved = 0
                best_loss = total_val_loss
                torch.save(tpr_encoder.state_dict(), weight_file)
            else:
                count_epochs_not_improved += 1
                if count_epochs_not_improved == patience:
                    print('Finished training early')
                    break

    return best_loss
