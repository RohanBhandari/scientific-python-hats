# coding: utf-8
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.model_selection import cross_val_score
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from keras.models import Sequential, Model, model_from_json
from keras.optimizers import SGD
from keras.layers import Input, Activation, Dense, Convolution2D, MaxPooling2D, Dropout, Flatten
from keras.utils import np_utils
from keras.wrappers.scikit_learn import KerasClassifier
from keras.callbacks import EarlyStopping
from keras.layers import Merge, merge
from keras import backend as K
import numpy as np
import pandas as pd
import sys, glob, argparse
from itertools import cycle
from scipy import interp
# fix random seed for reproducibility
seed = 7
np.random.seed(seed)

def normalize(x):
    # utility function to normalize a tensor by its L2 norm
    return x / (K.sqrt(K.mean(K.square(x))) + 1e-5)

class JetImageGenerator(object):
    def __init__(self,batch_size=32):
        # structure of dataframe
        self.jet_columns = ['run', 'lumi', 'event', 'met', 'sumet', 'rho', 'pthat', 'mcweight',
                            'njet_ak7', 'jet_pt_ak7', 'jet_eta_ak7', 'jet_phi_ak7', 'jet_E_ak7',
                            'jet_msd_ak7', 'jet_area_ak7', 'jet_jes_ak7', 'jet_tau21_ak7', 'jet_isW_ak7',
                            'jet_ncand_ak7','ak7pfcand_ijet']
        #self.NDIM = self.jet_columns.index('jet_isW_ak7')
        self.NDIM = self.jet_columns.index('jet_jes_ak7')

        self.cand_columns = ['event', 'jet_pt_ak7', 'jet_isW_ak7', 'ak7pfcand_pt', 'ak7pfcand_eta',
                             'ak7pfcand_phi', 'ak7pfcand_id', 'ak7pfcand_charge', 'ak7pfcand_ijet']

        self.batch_size = batch_size

        self.categories = ['TT','QCD']
        self.file_pattern = {
            'TT': 'output_TT/*.npy',
            'QCD': 'output_QCD*/*.npy'
        }
        
        self.preselection = {
            'TT': lambda df: df[(df.jet_pt_ak7 > 200) & (df.jet_pt_ak7 < 500) &  (df.jet_isW_ak7==1)],
            'QCD': lambda df: df[(df.jet_pt_ak7 > 200) & (df.jet_pt_ak7 < 500) &  (df.jet_isW_ak7==0)],
        }
                
        # list of files
        self.inputs = {}
        for cat in self.categories:
            self.inputs[cat] = glob.glob(self.file_pattern[cat])
        
        
        
    def _load_category(self,category,i):
        if i<len(self.inputs[category]):
            try:
                fname = self.inputs[category][i]
                #print category, i, fname
                params = np.load(fname)
                jet_df = pd.DataFrame(params,columns=self.jet_columns)
                cand_df = pd.DataFrame(params,columns=self.cand_columns)
                jet_df.drop_duplicates(inplace=True)
                jet_df = self.preselection[category](jet_df)
                cand_df = self.preselection[category](cand_df)
                return jet_df, cand_df
            except:
                print 'bad file: %s'%fname
                if fname in self.inputs[category]: self.inputs[category].remove(fname)
        return pd.DataFrame(), pd.DataFrame()
    
    def generator(self,test=False,crossvalidation=False):

        percat = self.batch_size/len(self.categories)

        icat = {cat:0 for cat in self.categories}
        cat_X = {cat:np.array([]) for cat in self.categories}
        cat_y = {cat:np.array([]) for cat in self.categories}
        cat_z = {cat:np.array([]) for cat in self.categories}
        cat_t = {cat:np.array([]) for cat in self.categories}
        
        kfold = StratifiedKFold(n_splits=2, shuffle=True,  random_state=seed)
        
        while True:
            # stop iteration
            #for cat in self.categories:
            #    if len(cat_y[cat])<percat and icat[cat]>=len(self.inputs[cat]):
            #        raise StopIteration
            
            # load data
            for cat in self.categories:
                while len(cat_y[cat])<percat:
                    # load from file
                    jet_df, cand_df = self._load_category(cat,icat[cat]%len(self.inputs[cat])) # allow infinite looping
                    # get the image from pf cands
                    if K.image_dim_ordering()=='tf':
                        jet_images = np.zeros((len(jet_df), nx, ny, 1))
                    else:        
                        jet_images = np.zeros((len(jet_df), 1, nx, ny))
                    njets = 0
                    for i in range(0,len(jet_df)):
                        njets+=1
                        # get the ith jet
                        df_cand_i = cand_df[(cand_df['ak7pfcand_ijet'] == jet_df['ak7pfcand_ijet'].iloc[i]) & (cand_df['event'] == jet_df['event'].iloc[i])]
                        # relative eta
                        x = df_cand_i['ak7pfcand_eta']-df_cand_i['ak7pfcand_eta'].iloc[0]
                        # relative phi
                        y = df_cand_i['ak7pfcand_phi']-df_cand_i['ak7pfcand_phi'].iloc[0]
                        weights = df_cand_i['ak7pfcand_pt'] # pt of candidate is the weight
                        x,y = rotate_and_reflect(x,y,weights)
                        hist, xedges, yedges = np.histogram2d(x, y,weights=weights, bins=(xbins,ybins))
                        for ix in range(0,nx):
                            for iy in range(0,ny):
                                if K.image_dim_ordering()=='tf':
                                    jet_images[i,ix,iy,0] = hist[ix,iy]
                                else:
                                    jet_images[i,0,ix,iy] = hist[ix,iy]
                    # split them to test and train
                    X = jet_images
                    y = jet_df.values[:,self.NDIM]
                    z = jet_df.values[:,self.jet_columns.index('jet_pt_ak7')]
                    t = jet_df.values[:,self.jet_columns.index('jet_eta_ak7')]
                    #encoder = LabelEncoder()
                    #encoder.fit(y)
                    #encoded_y = encoder.transform(y)
                    #data_train, data_test = list(kfold.split(X, encoded_y))[int(crossvalidation)]
                    mixed = list(zip(X,y,z,t))
                    np.random.shuffle(mixed) 
                    data_train = mixed[:int(len(mixed)*0.4)]
                    data_test = mixed[int(len(mixed)*0.4):]
                    # select test or train
                    sample = data_test if test else data_train
                    X = np.array([C[0] for C in sample])
                    y = np.array([C[1] for C in sample])
                    z = np.array([C[2] for C in sample])
                    t = np.array([C[3] for C in sample])
                    cat_X[cat] = np.vstack((cat_X[cat],X)) if cat_X[cat].size else X
                    cat_y[cat] = np.hstack((cat_y[cat],y)) if cat_y[cat].size else y
                    cat_z[cat] = np.hstack((cat_z[cat],z)) if cat_z[cat].size else z
                    cat_t[cat] = np.hstack((cat_t[cat],t)) if cat_t[cat].size else t
                    icat[cat] += 1

            # build combined sample based on batch_size
            all_X = np.array([])
            all_y = np.array([])
            all_z = np.array([])
            all_t = np.array([])
            for cat in self.categories:
                X = cat_X[cat][:percat]
                y = cat_y[cat][:percat]
                z = cat_z[cat][:percat]
                t = cat_t[cat][:percat]
                cat_X[cat] = cat_X[cat][percat:]
                cat_y[cat] = cat_y[cat][percat:]
                cat_z[cat] = cat_z[cat][percat:]
                cat_t[cat] = cat_t[cat][percat:]
                all_X = np.vstack((all_X,X)) if all_X.size else X
                all_y = np.hstack((all_y,y)) if all_y.size else y
                all_z = np.hstack((all_z,z)) if all_z.size else z
                all_t = np.hstack((all_t,t)) if all_t.size else t

            #yield [all_X, all_z, all_t], all_y
            yield all_X, all_y

# rotation + (possible) reflection needed later
def rotate_and_reflect(x,y,w):
    rot_x = []
    rot_y = []
    theta = 0
    maxPt = -1
    for ix, iy, iw in zip(x, y, w):
        dv = np.matrix([[ix],[iy]])-np.matrix([[x.iloc[0]],[y.iloc[0]]])
        dR = np.linalg.norm(dv)
        thisPt = iw
        if dR > 0.35 and thisPt > maxPt:
            maxPt = thisPt
            # rotation in eta-phi plane c.f  https://arxiv.org/abs/1407.5675 and https://arxiv.org/abs/1511.05190:
            # theta = -np.arctan2(iy,ix)-np.radians(90)
            # rotation by lorentz transformation c.f. https://arxiv.org/abs/1704.02124:
            px = iw * np.cos(iy)
            py = iw * np.sin(iy)
            pz = iw * np.sinh(ix)
            theta = np.arctan2(py,pz)+np.radians(90)
            
    c, s = np.cos(theta), np.sin(theta)
    R = np.matrix('{} {}; {} {}'.format(c, -s, s, c))
    for ix, iy, iw in zip(x, y, w):
        # rotation in eta-phi plane:
        #rot = R*np.matrix([[ix],[iy]])
        #rix, riy = rot[0,0], rot[1,0]
        # rotation by lorentz transformation
        px = iw * np.cos(iy)
        py = iw * np.sin(iy)
        pz = iw * np.sinh(ix)
        rot = R*np.matrix([[py],[pz]])
        rix, riy = np.arcsinh(rot[1,0]/iw), np.arcsin(rot[0,0]/iw)
        rot_x.append(rix)
        rot_y.append(riy)
        
    # now reflect if leftSum > rightSum
    leftSum = 0
    rightSum = 0
    for ix, iy, iw in zip(x, y, w):
        if ix > 0: 
            rightSum += iw
        elif ix < 0:
            leftSum += iw
    if leftSum > rightSum:
        ref_x = [-1.*rix for rix in rot_x]
        ref_y = rot_y
    else:
        ref_x = rot_x
        ref_y = rot_y
    
    return np.array(ref_x), np.array(ref_y)

def getInputs():
    # get input numpy arrays
    inputs = {}
    inputs['TT'] = glob.glob('output_TT/*job5*.npy')
    inputs['QCD120'] = glob.glob('output_QCD120/*job0*.npy')
    inputs['QCD170'] = glob.glob('output_QCD170/*job0*.npy')
    inputs['QCD300'] = glob.glob('output_QCD300/*job0*.npy')
    inputs['QCD470'] = glob.glob('output_QCD470/*job0*.npy')
    return inputs

def openFiles(inputs):
    list_params = {}
    params = {}
    for key, input_files in inputs.iteritems():
        list_params[key] = []
        for in_file in input_files:
            try:
                arr = np.load(in_file)
                list_params[key].append(arr)
            except ValueError:
                print 'bad file: %s'%in_file
        params[key] = np.concatenate(list_params[key])
    return params

def convertToPandas(params,verbose):
    # convert to pandas dataframe
    df_dict = {}
    df_dict['TT'] = pd.DataFrame(params['TT'],columns=['jet_pt_ak7','jet_tau21_ak7','jet_msd_ak7','jet_ncand_ak7','jet_isW_ak7', 'pthat','mcweight'])
    for QCDbin in ['QCD120','QCD170','QCD300','QCD470']:
        df_dict[QCDbin] = pd.DataFrame(params[QCDbin],columns=['jet_pt_ak7','jet_tau21_ak7','jet_msd_ak7','jet_ncand_ak7','jet_isW_ak7', 'pthat','mcweight'])

    df_dict['TT'] = df_dict['TT'].drop_duplicates()
    df_dict['TT'] =  df_dict['TT'][(df_dict['TT'].jet_pt_ak7 > 200) & (df_dict['TT'].jet_pt_ak7 < 500) &  (df_dict['TT'].jet_isW_ak7==1)]

    for QCDbin in ['QCD120','QCD170','QCD300','QCD470']:
        df_dict[QCDbin] = df_dict[QCDbin].drop_duplicates()
        df_dict[QCDbin] =  df_dict[QCDbin][(df_dict[QCDbin].jet_pt_ak7 > 200) & (df_dict[QCDbin].jet_pt_ak7 < 500) & (df_dict[QCDbin].jet_isW_ak7==0)]
        # take every 20th jet just to make the training faster and have a sample roughly the size of W jets
        df_dict[QCDbin] = df_dict[QCDbin].iloc[::20, :]
    
    df_dict['QCD'] = pd.concat([df_dict['QCD120'],df_dict['QCD170'],df_dict['QCD300'],df_dict['QCD470']])
    df = pd.concat([df_dict['TT'],df_dict['QCD']])

    if verbose:
        print params['TT'].dtype.names
        print 'number of W jets: %i'%len(df_dict['TT'])
        for QCDbin in ['QCD120','QCD170','QCD300','QCD470']:
            print 'number of QCD jets in bin %s: %i'%( QCDbin, len(df_dict[QCDbin]))
        print df_dict['TT'].iloc[:3]
        print df_dict['QCD'].iloc[:3]

    return df

# Model
def build_conv_model(nx=30, ny=30):
    """Test model.  Consists of several convolutional layers followed by dense layers and an output layer"""
    if K.image_dim_ordering()=='tf':
        input_layer = Input(shape=(nx, ny, 1))
    else:
        input_layer = Input(shape=(1, nx, ny))
    layer = Convolution2D(20, 7, 7, border_mode='same')(input_layer)
    layer = Activation('tanh')(layer)
    layer = MaxPooling2D(pool_size=(2,2))(layer)
    layer = Convolution2D(8, 3, 3, border_mode='same')(layer)
    layer = Activation('tanh')(layer)
    layer = MaxPooling2D(pool_size=(3,3))(layer)
    layer = Convolution2D(8, 3, 3, border_mode='same')(layer)
    layer = Activation('tanh')(layer)
    layer = MaxPooling2D(pool_size=(3,3))(layer)
    layer = Flatten()(layer)
    # additional features input
    #jet_pt_ak7_input = Input(shape=(1,), name='jet_pt_ak7_input')
    #jet_eta_ak7_input = Input(shape=(1,), name='jet_eta_ak7_input')
    #layer = merge([layer, jet_pt_ak7_input, jet_eta_ak7_input], mode='concat')
    layer = Dropout(0.20)(layer)
    layer = Dense(20)(layer)
    layer = Dropout(0.10)(layer)
    #output_layer = Dense(1, activation='sigmoid')(layer)
    output_layer = Dense(1, activation='relu', name='main_output')(layer)
    #model = Model(input=[input_layer,jet_pt_ak7_input,jet_eta_ak7_input], output=output_layer)
    model = Model(input=input_layer, output=output_layer)
    model.compile(optimizer='adam', loss='mean_squared_error')
    #model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
    return model

def fitModels(nx,ny,verbosity,debug):
    # Run classifier with cross-validation and plot ROC curves
    #kfold = StratifiedKFold(n_splits=2, shuffle=True,  random_state=seed)
    #early_stopping = EarlyStopping(monitor='val_loss', patience=10)

    mean_tpr = 0.0
    mean_fpr = np.linspace(0, 1, 100)
    colors = cycle(['cyan', 'indigo', 'seagreen', 'yellow', 'blue', 'darkorange', 'red', 'black', 'green', 'brown'])
    lw = 2

    i = 0
    histories = []
    models = []
    for cv, color in zip(range(0,1), colors):
        conv_model = build_conv_model(nx,ny)
        if verbosity:
            conv_model.summary()
        early_stopping = EarlyStopping(monitor='val_loss', patience=100)
        jetImageGenerator = JetImageGenerator()
        history = conv_model.fit_generator(jetImageGenerator.generator(crossvalidation=int(cv)), 128, validation_data=jetImageGenerator.generator(test=True), nb_val_samples=128, nb_epoch=25, verbose=verbosity, callbacks=[early_stopping])
        histories.append(history)
        models.append(conv_model)
    return models

def saveModel(model,verbose):
    # serialize model to JSON
    model_json = model.to_json()
    with open("model.json", "w") as json_file:
        json_file.write(model_json)
    # serialize weights to HDF5
    model.save_weights("model.h5")
    print("Saved model to disk")

def loadModel(verbose):
    # load json and create model
    json_file = open('model.json', 'r')
    loaded_model_json = json_file.read()
    json_file.close()
    loaded_model = model_from_json(loaded_model_json)
    # load weights into new model
    loaded_model.load_weights("model.h5")
    if verbose:
        print("Loaded model from disk")
    return [loaded_model]

####################
# Global Variables #
####################
nx = 30 # size of image in eta
ny = 30 # size of image in phi
xbins = np.linspace(-1.4,1.4,nx+1)
ybins = np.linspace(-1.4,1.4,ny+1)
def main(open_models,train_models,save_models,plot,debug,verbose):
    inputs = getInputs()
    params = openFiles(inputs)
    df = convertToPandas(params,verbose)
    if open_models:
        models = loadModel(verbose)
    else:
        models = fitModels(nx,ny,verbose,debug)
    if save_models and len(models)>=1:
        saveModel(models[0],verbose)
    if plot:
        import MLJEC_MCTruth_Plot as plotter
        df_dict_jet, df_dict_cand = plotter.prepare_jet_images(params, verbose)
        #plotter.plotJet(df_dict_jet, df_dict_cand,process='TT', njets_to_plot=1, nx=nx, ny=ny, xbins=xbins, ybins=ybins)
        #plotter.plotJet(df_dict_jet, df_dict_cand,process='QCD', njets_to_plot=1, nx=nx, ny=ny, xbins=xbins, ybins=ybins)
        #plotter.plot_ROC_curves(models[0])
        plotter.plot_JES(models[0])

if __name__ == '__main__':
    #program name available through the %(prog)s command
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""
Open files and train models for maching learning (ML) based JEC.""",
                                     epilog="""
And those are the options available. Deal with it.
""")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-o", "--open_models", help="load the models from a file", action="store_true")
    group.add_argument("-t", "--train_models", help="refit the models", action="store_true")
    parser.add_argument("-s", "--save_models", help="save the models and the weights", action="store_true")
    parser.add_argument("-p", "--plot", help="plot the ROC curves after training and testing", action="store_true")
    parser.add_argument("-d","--debug", help="Shows extra information in order to debug this program.",
                        action="store_true")
    parser.add_argument("-v","--verbose", help="print out additional information", action="store_true")
    parser.add_argument('--version', action='version', version='%(prog)s 2.0b')
    args = parser.parse_args()

    if(args.debug):
         print 'Number of arguments:', len(sys.argv), 'arguments.'
         print 'Argument List:', str(sys.argv)
         print "Argument ", args

    main(open_models=args.open_models,train_models=args.train_models,save_models=args.save_models,plot=args.plot,debug=args.debug,verbose=args.verbose)



