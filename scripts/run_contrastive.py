import time
from collections import deque
from itertools import chain

import numpy as np
import torch

from src.envs import make_vec_envs
from src.spatio_temporal import SpatioTemporalTrainer
from src.utils import get_argparser, visualize_activation_maps
from src.encoders import NatureCNN, ImpalaCNN
from src.appo import AppoTrainer
from src.cpc import CPCTrainer
from src.atari_zoo import get_atari_zoo_episodes
import wandb
import sys


def main():
    
    parser = get_argparser()
    #sys.argv = []
    args = parser.parse_args()
#     args.pretraining_steps = 1000
#     args.no_downsample = True
#     args.num_frame_stack = 1
#     args.collect_mode = "atari_zoo"
    device = torch.device("cuda:" + str(args.cuda_id) if torch.cuda.is_available() else "cpu")
    envs = make_vec_envs(args.env_name, args.seed, args.num_processes, num_frame_stack=args.num_frame_stack,
                         downsample=not args.no_downsample)
    spatial_features = 'spatial' in args.method
    if args.encoder_type == "Nature":
        encoder = NatureCNN(envs.observation_space.shape[0], downsample=not args.no_downsample, spatial_features=spatial_features)
    elif args.encoder_type == "Impala":
        encoder = ImpalaCNN(envs.observation_space.shape[0], downsample=not args.no_downsample, spatial_features=spatial_features)
    encoder.to(device)
    torch.set_num_threads(1)
    tags=['pretraining-only']
    wandb.init(project="curl-atari", entity="curl-atari", tags=tags)
    config = {
        'encoder_type': encoder.__class__.__name__,
        'obs_space': str(envs.observation_space.shape),
        'optimizer': 'Adam'
    }
    config.update(vars(args))
    wandb.config.update(config)

    config['obs_space'] = envs.observation_space.shape # weird hack
    if args.method == 'appo':
        trainer = AppoTrainer(encoder, config, device=device, wandb=wandb)
    if args.method == 'cpc':
        trainer = CPCTrainer(encoder, config, device=device, wandb=wandb)
    if args.method == 'spatial-appo':
        trainer = SpatioTemporalTrainer(encoder, config, device=device, wandb=wandb)

    if args.collect_mode == "random_agent":
        obs = envs.reset()
        episode_rewards = deque(maxlen=10)
        start = time.time()
        print('-------Collecting samples----------')
        episodes = [[[]] for _ in range(args.num_processes)]  # (n_processes * n_episodes * episode_len)
        for step in range(args.pretraining_steps // args.num_processes):
            # Take action using a random policy
            action = torch.tensor(np.array([np.random.randint(1, envs.action_space.n) for _ in range(args.num_processes)])) \
                .unsqueeze(dim=1).to(device)
            obs, reward, done, infos = envs.step(action)
            for i, info in enumerate(infos):
                if 'episode' in info.keys():
                    episode_rewards.append(info['episode']['r'])
                if done[i] != 1:
                    episodes[i][-1].append(obs[i].clone())
                else:
                    episodes[i].append([obs[i].clone()])

        episode_lens = []
        for i in range(args.num_processes):
            episode_lens += [len(episode) for episode in episodes[i]]
        print("Episode lengths: mean/std/min/max",
                np.mean(episode_lens), np.std(episode_lens),
                np.min(episode_lens), np.max(episode_lens))
        # Put episode frames on the GPU.
        for p in range(args.num_processes):
            for e in range(len(episodes[p])):
                episodes[p][e] = torch.stack(episodes[p][e])

        # Convert to 1d list from 2d list
        episodes = list(chain.from_iterable(episodes))
        episodes = [x for x in episodes if len(x) > args.batch_size]
    elif args.collect_mode == "atari_zoo":
        episodes, _ = get_atari_zoo_episodes(args.env_name, tags=tags, num_frame_stack=args.num_frame_stack, downsample= not args.no_downsample)
        episodes = [torch.from_numpy(ep).permute(0,3,1,2).float() for ep in episodes]
        
        
    
    
    
    inds = range(len(episodes))
    split_ind = int(0.8 * len(inds))

    tr_eps, val_eps = episodes[:split_ind], episodes[split_ind:]


    trainer.train(tr_eps, val_eps)
    # TODO: Better data selection. This failed for a run on Pong.
    # frames = episodes[200][:60, :, :, :]
    # visualize_activation_maps(encoder, frames, wandb)


if __name__ == "__main__":
    main()