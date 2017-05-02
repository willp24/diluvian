# -*- coding: utf-8 -*-
"""Flood-fill network creation and compilation using Keras."""


import inspect

import numpy as np

from keras.layers import (
        Convolution3D,
        Cropping3D,
        GaussianDropout,
        Input,
        merge,
        Permute,
        UpSampling3D
        )
from keras.layers.core import Activation
from keras.models import load_model as keras_load_model, Model
import keras.optimizers


def make_flood_fill_network(input_fov_shape, output_fov_shape, network_config):
    """Construct a stacked convolution module flood filling network.
    """
    image_input = Input(shape=tuple(input_fov_shape) + (1,), dtype='float32', name='image_input')
    mask_input = Input(shape=tuple(input_fov_shape) + (1,), dtype='float32', name='mask_input')
    ffn = merge([image_input, mask_input], mode='concat')

    # Convolve and activate before beginning the skip connection modules,
    # as discussed in the Appendix of He et al 2016.
    ffn = Convolution3D(network_config.convolution_filters,
                        network_config.convolution_dim[0],
                        network_config.convolution_dim[1],
                        network_config.convolution_dim[2],
                        init=network_config.initialization,
                        activation='relu',
                        border_mode='same')(ffn)

    contraction = (input_fov_shape - output_fov_shape) / 2
    if np.any(np.less(contraction, 0)):
        raise ValueError('Output FOV shape can not be larger than input FOV shape.')
    contraction_cumu = np.zeros(3, dtype=np.int32)
    contraction_step = np.divide(contraction, float(network_config.num_modules))

    for i in range(0, network_config.num_modules):
        ffn = add_convolution_module(ffn, network_config)
        contraction_dims = np.floor(i * contraction_step - contraction_cumu).astype(np.int32)
        if np.count_nonzero(contraction_dims):
            ffn = Cropping3D(zip(list(contraction_dims), list(contraction_dims)))(ffn)
            contraction_cumu += contraction_dims

    if np.any(np.less(contraction_cumu, contraction)):
        remainder = contraction - contraction_cumu
        ffn = Cropping3D(zip(list(remainder), list(remainder)))(ffn)

    mask_output = Convolution3D(1,
                                network_config.convolution_dim[0],
                                network_config.convolution_dim[1],
                                network_config.convolution_dim[2],
                                init=network_config.initialization,
                                border_mode='same',
                                name='mask_output',
                                activation=network_config.output_activation)(ffn)
    ffn = Model(input=[image_input, mask_input], output=[mask_output])

    return ffn


def add_convolution_module(model, network_config):
    model2 = Convolution3D(network_config.convolution_filters,
                           network_config.convolution_dim[0],
                           network_config.convolution_dim[1],
                           network_config.convolution_dim[2],
                           init=network_config.initialization,
                           activation='relu',
                           border_mode='same')(model)
    model2 = Convolution3D(network_config.convolution_filters,
                           network_config.convolution_dim[0],
                           network_config.convolution_dim[1],
                           network_config.convolution_dim[2],
                           init=network_config.initialization,
                           border_mode='same')(model2)
    model = merge([model, model2], mode='sum')
    # Note that the activation here differs from He et al 2016, as that
    # activation is not on the skip connection path. However, this is not
    # likely to be important, see:
    # http://torch.ch/blog/2016/02/04/resnets.html
    # https://github.com/gcr/torch-residual-networks
    model = Activation('relu')(model)
    if network_config.dropout_probability > 0.0:
        model = GaussianDropout(network_config.dropout_probability)(model)

    return model


def make_flood_fill_unet(input_fov_shape, output_fov_shape, network_config):
    """Construct a U-net flood filling network.
    """
    image_input = Input(shape=tuple(input_fov_shape) + (1,), dtype='float32', name='image_input')
    mask_input = Input(shape=tuple(input_fov_shape) + (1,), dtype='float32', name='mask_input')
    ffn = merge([image_input, mask_input], mode='concat')

    ffn = Convolution3D(network_config.convolution_filters,
                        network_config.convolution_dim[0],
                        network_config.convolution_dim[1],
                        network_config.convolution_dim[2],
                        init=network_config.initialization,
                        activation='relu',
                        border_mode='same')(ffn)

    ffn = add_unet_layer(ffn, network_config, 4, output_fov_shape)

    mask_output = Convolution3D(1,
                                network_config.convolution_dim[0],
                                network_config.convolution_dim[1],
                                network_config.convolution_dim[2],
                                init=network_config.initialization,
                                border_mode='same',
                                name='mask_output',
                                activation=network_config.output_activation)(ffn)
    ffn = Model(input=[image_input, mask_input], output=[mask_output])

    return ffn


def add_unet_layer(model, network_config, remaining_layers, output_shape):
    # Double number of channels at each layer.
    n_channels = 2 * model.get_shape().as_list()[-1]
    downsample = np.array([0, 1, 1])

    # First U convolution module.
    model = Convolution3D(n_channels,
                          network_config.convolution_dim[0],
                          network_config.convolution_dim[1],
                          network_config.convolution_dim[2],
                          init=network_config.initialization,
                          activation='relu',
                          border_mode='same')(model)
    model = Convolution3D(n_channels,
                          network_config.convolution_dim[0],
                          network_config.convolution_dim[1],
                          network_config.convolution_dim[2],
                          init=network_config.initialization,
                          activation='relu',
                          border_mode='same')(model)

    # Crop and pass forward to upsampling.
    contraction = (np.array(model.get_shape().as_list()[1:4]) - output_shape) / 2
    forward = Cropping3D(zip(list(contraction), list(contraction)))(model)
    if network_config.dropout_probability > 0.0:
        forward = GaussianDropout(network_config.dropout_probability)(forward)

    # Terminal layer of the U.
    if remaining_layers <= 0:
        return forward

    # Downsample and recurse.
    model = Convolution3D(n_channels,
                          network_config.convolution_dim[0],
                          network_config.convolution_dim[1],
                          network_config.convolution_dim[2],
                          subsample=list(downsample + 1),
                          init=network_config.initialization,
                          activation='relu',
                          border_mode='same')(model)
    model = add_unet_layer(model,
                           network_config,
                           remaining_layers - 1,
                           np.ceil(np.divide(output_shape, downsample.astype('float32') + 1.0)).astype(np.int32))

    # Upsample output of previous layer and merge with forward link.
    # TODO: This is a bad hack for convolutional upsampling. My old hack of
    # this used a custom layer, but this hack is used for the sake of using
    # simple Keras layers. This should be fixed when upgrading to Keras 2.
    model = UpSampling3D(list(downsample + 1))(model)
    model = Convolution3D(n_channels,
                          downsample[0] + 1,
                          downsample[1] + 1,
                          downsample[2] + 1,
                          init=network_config.initialization,
                          activation='relu',
                          border_mode='valid')(model)

    model = merge([forward, model], mode='concat')

    # Second U convolution module.
    model = Convolution3D(n_channels,
                          network_config.convolution_dim[0],
                          network_config.convolution_dim[1],
                          network_config.convolution_dim[2],
                          init=network_config.initialization,
                          activation='relu',
                          border_mode='same')(model)
    model = Convolution3D(n_channels,
                          network_config.convolution_dim[0],
                          network_config.convolution_dim[1],
                          network_config.convolution_dim[2],
                          init=network_config.initialization,
                          activation='relu',
                          border_mode='same')(model)

    return model


def compile_network(model, optimizer_config):
    optimizer_klass = getattr(keras.optimizers, optimizer_config.klass)
    optimizer_kwargs = inspect.getargspec(optimizer_klass.__init__)[0]
    optimizer_kwargs = {k: v for k, v in optimizer_config.__dict__.iteritems() if k in optimizer_kwargs}
    optimizer = optimizer_klass(**optimizer_kwargs)
    model.compile(loss='binary_crossentropy',
                  optimizer=optimizer)


def load_model(model_file, network_config):
    model = keras_load_model(model_file)

    # If necessary, wrap the loaded model to transpose the axes for both
    # inputs and outputs.
    if network_config.transpose:
        inputs = []
        perms = []
        for old_input in model.input_layers:
            input_shape = np.asarray(old_input.input_shape)[[3, 2, 1, 4]]
            new_input = Input(shape=tuple(input_shape), dtype=old_input.input_dtype, name=old_input.name)
            perm = Permute((3, 2, 1, 4), input_shape=tuple(input_shape))(new_input)
            inputs.append(new_input)
            perms.append(perm)

        old_outputs = model(perms)
        if not isinstance(old_outputs, list):
            old_outputs = [old_outputs]

        outputs = []
        for old_output in old_outputs:
            new_output = Permute((3, 2, 1, 4))(old_output)
            outputs.append(new_output)

        new_model = Model(input=inputs, output=outputs)

        # Monkeypatch the save to save just the underlying model.
        func_type = type(model.save)

        old_model = model

        def new_save(_, *args, **kwargs):
            old_model.save(*args, **kwargs)
        new_model.save = func_type(new_save, new_model)

        model = new_model

    return model
