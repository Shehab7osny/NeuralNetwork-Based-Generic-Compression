import wave, struct
import keras
import tensorflow as tf
import argparse
import noisereduce as nr
import numpy as np

# Extracting the essential data (amplitude,#samples,#channels, samplewidth, sampling rate )from the wave audio file
def dataFromWave(fname):
    """
    Reads a wav file to samples
    """
    f = wave.open(fname, 'rb')
    # Read Channel Number
    chans = f.getnchannels()
    # Get raw sample count
    samps = f.getnframes()
    # Get bit-width of samples
    sampwidth = f.getsampwidth()
    # Get sampling rate
    rate = f.getframerate()
    # Read samples
    if sampwidth == 3:  # have to read this one sample at a time
        s = ''
        for k in range(samps):
            fr = f.readframes(1)
            for c in range(0, 3 * chans, 3):
                s += '\0' + fr[c:(c + 3)]  # put TRAILING 0 to make 32-bit (file is little-endian)
    else:
        s = f.readframes(samps)
    f.close()
    # Unpack samples
    unpstr = '<{0}{1}'.format(samps * chans, {1: 'b', 2: 'h', 3: 'i', 4: 'i', 8: 'q'}[sampwidth])
    x = list(struct.unpack(unpstr, s))
    if sampwidth == 3:
        x = [k >> 8 for k in x]  # downshift to get +/- 2^24 with sign extension

    return x, chans, samps, sampwidth, rate

#Reconstructing the wave audio files using the data samples collected previously
def dataToWave(fname, data, chans, samps, sampwidth, rate):
    """
    Writes samples to a wav file
    """
    obj = wave.open(fname, 'wb')
    # Set parameters
    obj.setnchannels(chans)
    obj.setsampwidth(sampwidth)
    obj.setframerate(rate)
    # set up the packaging format
    packstr = "<{0}".format({1: 'b', 2: 'h', 3: 'i', 4: 'i', 8: 'q'}[sampwidth])
    # Package the samples
    for i in range(samps * chans):
        obj.writeframesraw(struct.pack(packstr, data[i]))
    obj.close()

# Bounding the audio amplitude to range from [-32768:32768]
def norm(x):
    """
    NN output isn't quite perfect, make sure it's bounded
    """
    # If we're outside allowable wav value, bound them
    if x < -32768:
        return -32768
    if x > 32767:
        return 32767
    return x

#Compressing the audio file and getting a .npz file as an output
def encode(in_file, out_file):
    """
        Takes in a file path to read (a wav file)
        and a file path to write the encoded file to
        example: python encode.py encode "path of file intended for compression" "path of directory where to save .npz file with the specified name for it "
        """
    # Loading the previously trained model
    autoencoder = keras.models.load_model("autoencoder.model")

    # constructing the encoder layers
    in_layer = keras.layers.Input(shape=(1, 441))
    encode = autoencoder.layers[1](in_layer)
    encode = autoencoder.layers[2](encode)
    encode = autoencoder.layers[3](encode)
    encode = autoencoder.layers[4](encode)
    encoder = keras.models.Model(in_layer, encode)

    # Read the file
    data, chans, samps, width, samp_rate = dataFromWave(in_file)

    # Turn the samples into a numpy array
    data = np.array(data)

    # Set our encoding frame width
    # Experimentally determined that 1/100th of a second has decent results
    rate = samp_rate // 100
    print('rate = ', rate)
    # Rescale integer samples over range [-32768,32767] to floats over range [0.0,1.0]
    data = data.astype(float) / float(pow(2, 15))
    data += 1.0
    data = data / 2.0
    print(('data.shape = ', data.shape))
    # Pad the samples with zeroes, if needed, to make the last encoding frame full
    n_in = len(data)
    print('n_in = ', n_in)
    p_size = n_in + (rate - (n_in % rate))
    print('p_size = ', p_size)
    padded = np.zeros((p_size,))
    print('padded.shape = ', padded.shape)
    padded[0:n_in] = data
    print('padded.shape = ', padded.shape)

    # Construct input layer
    inputs = padded.reshape(len(padded)//rate, 1, rate)

    # Encode the data
    encoded = encoder.predict(inputs)
    # Save the encoded data, as well as the important parameters
    np.savez_compressed(out_file, data=encoded, params=np.array([chans, samps, width, samp_rate]))

# Decompressing the .npz file to an audio file
def decode(in_file, out_file):
    """
    This function takes in a file prefix to a data/model file pair,
    and decodes a wav file from them at the provided location.
    example : python encode.py decode "path of the .npz file inteded for reconstructing" " path of directory where to save the reconstructed audio file "
    """
    # Load the model
    autoencoder = keras.models.load_model("autoencoder.model")

    #Constructing the decoder layers
    in_layer = keras.layers.Input(shape=(1, 441//8))
    decode = autoencoder.layers[-3](in_layer)
    decode = autoencoder.layers[-2](decode)
    decode = autoencoder.layers[-1](decode)
    decoder = keras.models.Model(in_layer, decode)
    # Load the data
    ins = np.load(in_file + ".npz")
    encoded = ins['data']
    chans = ins['params'][0]
    samps = ins['params'][1]
    width = ins['params'][2]
    samp_rate = ins['params'][3]
    # Run the decoder
    outputs = decoder.predict(encoded)

    # Build a wav file
    out = np.concatenate(np.concatenate(outputs))

    #Removing noise in the reconstructed file using thresholding and noise clipping
    noisy_part = out[out > 0.9]
    out = nr.reduce_noise(audio_clip=out, noise_clip=noisy_part)
    out = (((out * 2.0) - 1.0) * float(pow(2, 15))).astype(int)
    out = list(map(norm, out))

    dataToWave(out_file + ".wav", out, chans, samps, width, samp_rate)

#Argumets to run the script
def main():
    # Limit Keras Memory Usage to avoid crashes
    config = tf.ConfigProto()
    config.gpu_options.per_process_gpu_memory_fraction = 0.7
    session = tf.Session(config=config)

    # Do command line stuff
    parser = argparse.ArgumentParser(description='An experimental audio compressor using naive autoencoding.')
    subparsers = parser.add_subparsers(help='The mode in which to run')
    encode_parser = subparsers.add_parser('encode', help='Encode a wav file')
    encode_parser.add_argument('in_file', type=str, help='A wav file to be encoded.')
    encode_parser.add_argument('out_file', type=str,
                               help='The file path prefix for the encoded output files to be stored.')
    encode_parser.set_defaults(func=encode)
    decode_parser = subparsers.add_parser('decode', help='Decode an encoded wav file')
    decode_parser.add_argument('in_file', type=str, help='The file path prefix where the encoded files are found.')
    decode_parser.add_argument('out_file', type=str, help='The file path where the decoded wav should be stored.')
    decode_parser.set_defaults(func=decode)
    args = parser.parse_args()
    if args.func == encode or args.func == decode:
        args.func(args.in_file, args.out_file)


if __name__ == "__main__":
    main()