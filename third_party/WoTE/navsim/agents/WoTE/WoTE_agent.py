from typing import Any, List, Dict, Union
import json
import os
import time
import torch
import numpy as np
from torch.optim import Optimizer
from torch.optim.lr_scheduler import LRScheduler
import pytorch_lightning as pl
from torchvision import transforms

from nuplan.planning.simulation.trajectory.trajectory_sampling import TrajectorySampling

from navsim.agents.abstract_agent import AbstractAgent
from navsim.common.dataclasses import AgentInput, SensorConfig
from navsim.planning.training.abstract_feature_target_builder import (
    AbstractFeatureBuilder,
    AbstractTargetBuilder,
)
from navsim.common.dataclasses import Scene
import timm, cv2
from navsim.agents.WoTE.WoTE_model import WoTEModel
from navsim.agents.WoTE.WoTE_loss import compute_wote_loss
from navsim.agents.WoTE.WoTE_targets import WoTETargetBuilder
from navsim.agents.WoTE.WoTE_features import WoTEFeatureBuilder
from navsim.common.dataclasses import AgentInput, Trajectory, SensorConfig
import math
from torch.optim.lr_scheduler import _LRScheduler
from omegaconf import DictConfig, OmegaConf, open_dict
import torch.optim as optim

def build_from_configs(obj, cfg: DictConfig, **kwargs):
    if cfg is None:
        return None
    cfg = cfg.copy()
    if isinstance(cfg, DictConfig):
        OmegaConf.set_struct(cfg, False)
    type = cfg.pop('type')
    return getattr(obj, type)(**cfg, **kwargs)

class WoTEAgent(AbstractAgent):
    def __init__(
        self,
        config,
        trajectory_sampling: TrajectorySampling,
        lr: float,
        checkpoint_path: str = None,
        slice_indices=[3],
        resume_from_checkpoint=False,
        use_wm=False,
    ):
        super().__init__()
        self._trajectory_sampling = trajectory_sampling
        self._checkpoint_path = checkpoint_path
        self._lr = lr
        self.max_epochs = config.max_epochs if hasattr(config, 'max_epochs') else 100
        self.min_lr = config.min_lr if hasattr(config, 'min_lr') else 1e-6

        self.WoTE_model = WoTEModel(config)

        self.slice_indices = slice_indices
        self.is_eval = False
        self.config = config
        self._utmr_debug_step = 0
        self._utmr_current_token = None
        self._utmr_method = os.environ.get("UTMR_WOTE_METHOD", "")
        self._utmr_step_log_path = os.environ.get(
            "UTMR_WOTE_STEP_LOG",
            getattr(config, "utmr_step_log_path", ""),
        )

        if resume_from_checkpoint:
            self.initialize()

    def name(self) -> str:
        """Inherited, see superclass."""

        return self.__class__.__name__

    def initialize(self) -> None:
        """Inherited, see superclass."""
        if torch.cuda.is_available():
            state_dict: Dict[str, Any] = torch.load(self._checkpoint_path)["state_dict"]
        else:
            state_dict: Dict[str, Any] = torch.load(
                self._checkpoint_path, map_location=torch.device("cpu")
            )["state_dict"]
        
        if "agent.WoTE_model.trajectory_anchors" in state_dict:
            del state_dict["agent.WoTE_model.trajectory_anchors"]

        self.load_state_dict({k.replace("agent.", ""): v for k, v in state_dict.items()}, strict=False)

    def get_sensor_config(self) -> SensorConfig:
        """Inherited, see superclass."""
        return SensorConfig.build_tfu_sensors(self.slice_indices) 

    def get_target_builders(self) -> List[AbstractTargetBuilder]:
        return [
            WoTETargetBuilder(
                        trajectory_sampling=self._trajectory_sampling,
                        slice_indices=self.slice_indices,
                        sim_reward_dict_path=self.config.sim_reward_dict_path,
                        config=self.config,
                    ),
        ]

    def get_feature_builders(self) -> List[AbstractFeatureBuilder]:
        return [WoTEFeatureBuilder(self.slice_indices, self.config)]

    def forward(self, features: Dict[str, torch.Tensor], targets=None) -> Dict[str, torch.Tensor]:
        if not self.is_eval: #training
            return self.WoTE_model.forward_train(features, targets)
        else:
            return self.WoTE_model.forward_test(features)

    def compute_trajectory(self, agent_input: AgentInput) -> Trajectory:
        self.is_eval = True
        self.eval()
        start_time = time.perf_counter()
        features: Dict[str, torch.Tensor] = {}
        for builder in self.get_feature_builders():
            features.update(builder.compute_features(agent_input))

        features = {k: v.unsqueeze(0) for k, v in features.items()}

        with torch.no_grad():
            predictions = self.forward(features)
            poses = predictions["trajectory"].squeeze(0).numpy()

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        self._maybe_write_utmr_step(agent_input, predictions, latency_ms)
        return Trajectory(poses)

    def _tensor_to_list(self, value):
        if isinstance(value, torch.Tensor):
            value = value.detach().cpu()
            if value.numel() == 1:
                return value.item()
            return value.tolist()
        return value

    def _candidate_speeds_kmh(self, trajectories: torch.Tensor) -> List[float]:
        poses = trajectories.detach().cpu().squeeze(0).numpy()
        interval = float(getattr(self.config.trajectory_sampling, "interval_length", 0.5))
        speeds = []
        for trajectory in poses:
            xy = trajectory[:, :2]
            if len(xy) < 2:
                speeds.append(0.0)
                continue
            distances = np.linalg.norm(np.diff(xy, axis=0), axis=1)
            speeds.append(float(np.mean(distances / interval) * 3.6))
        return speeds

    def _maybe_write_utmr_step(
        self,
        agent_input: AgentInput,
        predictions: Dict[str, torch.Tensor],
        latency_ms: float,
    ) -> None:
        if not self._utmr_step_log_path:
            return

        ego_velocity = np.asarray(agent_input.ego_statuses[-1].ego_velocity, dtype=np.float32)
        row = {
            "token": self._utmr_current_token,
            "step": self._utmr_debug_step,
            "method_variant": self._utmr_method,
            "latency_ms": latency_ms,
            "ego_speed_kmh": float(np.linalg.norm(ego_velocity) * 3.6),
            "coarse_scores": self._tensor_to_list(predictions["final_rewards"].squeeze(0)),
            "candidate_speeds_kmh": self._candidate_speeds_kmh(predictions["all_trajectory"]),
        }

        optional_keys = {
            "utmr_entropy": "entropy",
            "utmr_margin": "margin",
            "utmr_triggered": "triggered",
            "utmr_selected_indices": "selected_index",
            "utmr_baseline_indices": "baseline_index",
            "utmr_feasible_count": "feasible_count",
            "utmr_feasible_mask": "feasible_mask",
            "utmr_fine_scores": "fine_scores_full",
            "utmr_rerank_accepted": "rerank_accepted",
            "sim_rewards": "sim_rewards",
        }
        for source_key, output_key in optional_keys.items():
            if source_key in predictions:
                row[output_key] = self._tensor_to_list(predictions[source_key].squeeze(0))

        os.makedirs(os.path.dirname(os.path.abspath(self._utmr_step_log_path)), exist_ok=True)
        with open(self._utmr_step_log_path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, separators=(",", ":")) + "\n")
        self._utmr_debug_step += 1
    
    def compute_loss(
        self,
        features: Dict[str, torch.Tensor],
        targets: Dict[str, torch.Tensor],
        predictions: Dict[str, torch.Tensor],
    ) -> torch.Tensor:
        return compute_wote_loss(targets, predictions, self.config)

    def get_optimizers(self) -> Union[Optimizer, Dict[str, Union[Optimizer, LRScheduler]]]:
        use_coslr_opt = self.config.use_coslr_opt if hasattr(self.config, 'use_coslr_opt') else False
        if use_coslr_opt:
            return self.get_coslr_optimizers()
        else:
            return torch.optim.Adam(self.WoTE_model.parameters(), lr=self._lr)
    
    def get_coslr_optimizers(self):
        optimizer_cfg = dict(type=self.config.optimizer_type, 
                            lr=self._lr, 
                            weight_decay=self.config.weight_decay,
                            paramwise_cfg=self.config.opt_paramwise_cfg
                            )
        scheduler_cfg = dict(type=self.config.scheduler_type,
                            milestones=self.config.lr_steps,
                            gamma=0.1,
        )

        optimizer_cfg = DictConfig(optimizer_cfg)
        scheduler_cfg = DictConfig(scheduler_cfg)
        
        with open_dict(optimizer_cfg):
            paramwise_cfg = optimizer_cfg.pop('paramwise_cfg', None)

        if paramwise_cfg:
            params = []
            pgs = [[] for _ in paramwise_cfg['name']]

            for k, v in self.WoTE_model.named_parameters():
                in_param_group = True
                for i, (pattern, pg_cfg) in enumerate(paramwise_cfg['name'].items()):
                    if pattern in k:
                        pgs[i].append(v)
                        in_param_group = False
                if in_param_group:
                    params.append(v)
        else:
            params = self.WoTE_model.parameters()

        optimizer = build_from_configs(optim, optimizer_cfg, params=params)
        # import ipdb; ipdb.set_trace()
        if paramwise_cfg:
            for pg, (_, pg_cfg) in zip(pgs, paramwise_cfg['name'].items()):
                cfg = {}
                if 'lr_mult' in pg_cfg:
                    cfg['lr'] = optimizer_cfg['lr'] * pg_cfg['lr_mult']
                optimizer.add_param_group({'params': pg, **cfg})

        # scheduler = build_from_configs(optim.lr_scheduler, scheduler_cfg, optimizer=optimizer)
        scheduler = WarmupCosLR(
            optimizer=optimizer,
            lr=self._lr,
            min_lr=self.min_lr,
            epochs=self.max_epochs,
            warmup_epochs=3,
        )

        if 'interval' in scheduler_cfg:
            scheduler = {'scheduler': scheduler, 'interval': scheduler_cfg['interval']}

        return {'optimizer': optimizer, 'lr_scheduler': scheduler}

class WarmupCosLR(_LRScheduler):
    def __init__(
        self, optimizer, min_lr, lr, warmup_epochs, epochs, last_epoch=-1, verbose=False
    ) -> None:
        self.min_lr = min_lr
        self.lr = lr
        self.epochs = epochs
        self.warmup_epochs = warmup_epochs
        super(WarmupCosLR, self).__init__(optimizer, last_epoch, verbose)

    def state_dict(self):
        """Returns the state of the scheduler as a :class:`dict`.

        It contains an entry for every variable in self.__dict__ which
        is not the optimizer.
        """
        return {
            key: value for key, value in self.__dict__.items() if key != "optimizer"
        }

    def load_state_dict(self, state_dict):
        """Loads the schedulers state.

        Args:
            state_dict (dict): scheduler state. Should be an object returned
                from a call to :meth:`state_dict`.
        """
        self.__dict__.update(state_dict)

    def get_init_lr(self):
        lr = self.lr / self.warmup_epochs
        return lr

    def get_lr(self):
        if self.last_epoch < self.warmup_epochs:
            lr = self.lr * (self.last_epoch + 1) / self.warmup_epochs
        else:
            lr = self.min_lr + 0.5 * (self.lr - self.min_lr) * (
                1
                + math.cos(
                    math.pi
                    * (self.last_epoch - self.warmup_epochs)
                    / (self.epochs - self.warmup_epochs)
                )
            )
        if "lr_scale" in self.optimizer.param_groups[0]:
            return [lr * group["lr_scale"] for group in self.optimizer.param_groups]

        return [lr for _ in self.optimizer.param_groups]
