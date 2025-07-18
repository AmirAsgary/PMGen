##############################MODIFIED SCRIPT OF AFFINE###########################################
#######SOURCE: https://github.com/phbradley/alphafold_finetune ################

FREDHUTCH_HACKS = False # silly stuff Phil added for running on Hutch servers
if FREDHUTCH_HACKS:
    import os
    from shutil import which
    os.environ['XLA_FLAGS']='--xla_gpu_force_compilation_parallelism=1'
    os.environ["TF_FORCE_UNIFIED_MEMORY"] = '1'
    os.environ["XLA_PYTHON_CLIENT_MEM_FRACTION"] = '2.0'
    assert which('ptxas') is not None

import json
### added to not show redundant warning and log outputs from tensorflow
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning)
###
import argparse

parser = argparse.ArgumentParser(
    description="Run simple template-based alphafold inference",
    epilog = f'''
Examples:

# this command will build models and compute confidence scores for
# all 10mer peptides in HCV_POLG77 bound to HLA-A*02:01, using the default
# alphafold model_2_ptm parameters. You would need to change the --data_dir
# argument to point to the location of the folder containing the alphafold
# params/ subfolder.

python3 run_prediction.py --targets examples/pmhc_hcv_polg_10mers/targets.tsv --data_dir /home/pbradley/csdat/alphafold/data/ --outfile_prefix polg_test1 --model_names model_2_ptm --ignore_identities


    ''',
    formatter_class=argparse.RawDescriptionHelpFormatter,
)

parser.add_argument('--outfile_prefix',
                    help='Prefix that will be prepended to the output '
                    'filenames')
parser.add_argument('--final_outfile_prefix',
                    help='Prefix that will be prepended to the final output '
                    'tsv filename')
parser.add_argument('--targets', required=True, help='File listing the targets to '
                    'be modeled. See description of file format in the github '
                    'README and also examples in the examples/*/*tsv')
parser.add_argument('--data_dir', help='Location of AlphaFold params/ folder')

parser.add_argument('--model_names', type=str, nargs='*', default=['model_2_ptm'])
parser.add_argument('--model_params_files', type=str, nargs='*')

parser.add_argument('--verbose', action='store_true')
parser.add_argument('--ignore_identities', action='store_true',
                    help='Ignore the sequence identities column in the templates '
                    'alignment files. Useful when modeling many different peptides '
                    'using the same alignment file.')
parser.add_argument('--no_pdbs', action='store_true', help='Dont write out pdbs')
parser.add_argument('--terse', action='store_true', help='Dont write out pdbs or '
                    'matrices with alphafold confidence values')
parser.add_argument('--no_resample_msa', action='store_true', help='Dont randomly '
                    'resample from the MSA during recycling. Perhaps useful for '
                    'testing...')
# added by Amir for PMGen pipline
parser.add_argument('--num_recycles', type=int, nargs='*', default=[3])
parser.add_argument('--shuffle_templates', action='store_true', help='if set shuffles the templates. requires to have use_template column in aln files')
parser.add_argument('--num_templates', type=int, nargs='*', default=4, help='num templates to use after shuffling.')

parser.add_argument('--no_initial_guess', action='store_true', default=False, help='When active, no intial guess is used to direct modeling and only template is used.')
parser.add_argument('--return_all_outputs', action='store_true', default=False, help='Save all alphafold outputs including evoformer output')
parser.add_argument('--use_msa', action='store_true', default=False, help='If Enabled, use MSA for prediction. If not, only template is used.')
args = parser.parse_args()

import os
import sys
from os.path import exists
import itertools
import numpy as np
import pandas as pd
import predict_utils

targets = pd.read_table(args.targets) #DEBUG
lens = [len(x.target_chainseq.replace('/',''))
        for x in targets.itertuples()]
crop_size = max(lens)

if args.verbose:
    import jax
    from os import popen # just to get hostname for logging, not necessary
    # print some logging info
    platform = jax.local_devices()[0].platform
    hostname = popen('hostname').readlines()[0].strip()

    print('cmd:', ' '.join(sys.argv))
    print('local_device:', platform, 'hostname:', hostname, 'num_targets:',
          targets.shape[0], 'max_len=', crop_size)

sys.stdout.flush()
print('###########DEBUG', args.model_names)
model_runners = predict_utils.load_model_runners(
    args.model_names,
    crop_size,
    args.data_dir,
    model_params_files=args.model_params_files,
    resample_msa_in_recycling = not args.no_resample_msa,
    num_recycle = args.num_recycles[0],
    args = args
)

final_dfl = []
for counter, targetl in targets.iterrows():
    print('START:', counter, 'of', targets.shape[0])

    alignfile = targetl.templates_alignfile
    if not args.no_initial_guess: # added by Amir for initial guess condition and getting dict from input tsv
        template_pdb_dict = targetl.template_pdb_dict
        with open(template_pdb_dict, 'r') as f:
            template_pdb_dict = json.load(f)
    else: # added by Amir for initial guess condition and getting dict from input tsv
        template_pdb_dict = None
            

    print(alignfile)
    assert exists(alignfile)

    query_chainseq = targetl.target_chainseq
    if 'outfile_prefix' in targetl:
        outfile_prefix = targetl.outfile_prefix
    else:
        assert args.outfile_prefix is not None
        if 'targetid' in targetl:
            outfile_prefix = args.outfile_prefix+targetl.targetid
        else:
            outfile_prefix = f'{args.outfile_prefix}_T{counter}'

    query_sequence = query_chainseq.replace('/','')
    num_res = len(query_sequence)

    data = pd.read_table(alignfile) #DEBUG
    if args.shuffle_templates:
        if isinstance(args.num_templates, list):
            num_templates = args.num_templates[0]  # Take the first element if it's a list
        else:
            num_templates = args.num_templates  # Use it directly if it's not a list
        print('shuffle_templates flag == True, templates to use are:')
        try:
            data = data[data['use_template'] == 1].reset_index(drop=True)
            data = data.sample(frac=1).reset_index(drop=True)
            data = data.iloc[:int(num_templates)]
            print('Could shuffle templates and choose only use_tempalte==1')
        except:
            data = data.sample(frac=1).reset_index(drop=True)
            data = data.iloc[:int(num_templates)]
        
        
    cols = ('template_pdbfile target_to_template_alignstring identities '
            'target_len template_len'.split())
    template_features_list = []
    for tnum, row in data.iterrows():
        #(template_pdbfile, target_to_template_alignstring,
        # identities, target_len, template_len) = line[cols]

        assert row.target_len == len(query_sequence), f'{row.target_len},{len(query_sequence)}'
        target_to_template_alignment = {
            int(x.split(':')[0]) : int(x.split(':')[1]) # 0-indexed
            for x in row.target_to_template_alignstring.split(';')
        }

        template_name = f'T{tnum:03d}' # dont think this matters
        template_features = predict_utils.create_single_template_features(
            query_sequence, row.template_pdbfile, target_to_template_alignment,
            template_name, allow_chainbreaks=True, allow_skipped_lines=True,
            expected_identities = None if args.ignore_identities else row.identities,
            expected_template_len = row.template_len,
        )
        template_features_list.append(template_features)



    all_template_features = predict_utils.compile_template_features(
        template_features_list)

    if not args.use_msa: # added by Amir for using msa or not
        # if we are not using MSA, we need to set the deletion matrix
        msa=[query_sequence]
        deletion_matrix=[[0]*len(query_sequence)]
    else: # added by Amir for using msa or not
        # generate arbeitary msa from input sequence
        msa = [query_sequence] + msa
        

   

    all_metrics = predict_utils.run_alphafold_prediction(
        query_sequence=query_sequence,
        msa=msa,
        deletion_matrix=deletion_matrix,
        chainbreak_sequence=query_chainseq,
        template_features=all_template_features,
        model_runners=model_runners,
        out_prefix=outfile_prefix,
        crop_size=crop_size,
        dump_pdbs = not (args.no_pdbs or args.terse),
        dump_metrics = not args.terse,
        template_pdb_dict = template_pdb_dict, # added by Amir for getting pandora data
        no_initial_guess=args.no_initial_guess,
        return_all_outputs=args.return_all_outputs
    )


    outl = targetl.copy()
    for model_name, metrics in all_metrics.items():
        plddts = metrics['plddt']
        paes = metrics.get('predicted_aligned_error', None)

        cs = query_chainseq.split('/')
        chain_stops = list(itertools.accumulate(len(x) for x in cs))
        chain_starts = [0]+chain_stops[:-1]
        nres = chain_stops[-1]
        assert nres == num_res
        outl[model_name+'_plddt'] = np.mean(plddts[:nres])
        if paes is not None:
            outl[model_name+'_pae'] = np.mean(paes[:nres,:nres])
        for chain1,(start1,stop1) in enumerate(zip(chain_starts, chain_stops)):
            outl[f'{model_name}_plddt_{chain1}'] = np.mean(plddts[start1:stop1])

            if paes is not None:
                for chain2 in range(len(cs)):
                    start2, stop2 = chain_starts[chain2], chain_stops[chain2]
                    pae = np.mean(paes[start1:stop1,start2:stop2])
                    outl[f'{model_name}_pae_{chain1}_{chain2}'] = pae
    final_dfl.append(outl)

if args.final_outfile_prefix:
    outfile_prefix = args.final_outfile_prefix
elif args.outfile_prefix:
    outfile_prefix = args.outfile_prefix
elif 'outfile_prefix' in targets.columns:
    outfile_prefix = targets.outfile_prefix.iloc[0]
else:
    outfile_prefix = None

if outfile_prefix:
    outfile = f'{outfile_prefix}_final.tsv'
    pd.DataFrame(final_dfl).to_csv(outfile, sep='\t', index=False)
    print('made:', outfile)

