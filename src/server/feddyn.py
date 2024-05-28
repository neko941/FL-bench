from argparse import ArgumentParser, Namespace
from collections import OrderedDict
from typing import Any

import torch

from src.server.fedavg import FedAvgServer
from src.client.feddyn import FedDynClient
from src.utils.tools import trainable_params, NestedNamespace, vectorize
from src.utils.tools import NestedNamespace


def get_feddyn_args(args_list=None) -> Namespace:
    parser = ArgumentParser()
    parser.add_argument("--alpha", type=float, default=0.01)
    parser.add_argument("--max_grad_norm", type=float, default=10)
    return parser.parse_args(args_list)


class FedDynServer(FedAvgServer):
    def __init__(
        self,
        args: NestedNamespace,
        algo: str = "FedDyn",
        unique_model=False,
        use_fedavg_client_cls=False,
        return_diff=False,
    ):
        super().__init__(args, algo, unique_model, use_fedavg_client_cls, return_diff)
        self.init_trainer(FedDynClient)
        param_numel = vectorize(self.global_model_params).numel()
        self.nabla = [torch.zeros(param_numel) for _ in range(self.client_num)]
        self.clients_weight = torch.tensor(
            [len(self.data_indices[i]["train"]) for i in self.train_clients]
        )
        self.clients_weight = (
            self.clients_weight / self.clients_weight.sum() * len(self.train_clients)
        )

    def package(self, client_id: int):
        server_package = super().package(client_id)
        server_package["alpha"] = (
            self.args.feddyn.alpha / self.clients_weight[client_id]
        )
        server_package["nabla"] = self.nabla[client_id]
        return server_package

    def aggregate(self, clients_package: OrderedDict[int, dict[str, Any]]):
        params_list = [
            list(package["regular_model_params"].values())
            for package in clients_package.values()
        ]
        weights = torch.ones(len(params_list)) / len(params_list)
        avg_params = [
            (torch.stack(params, dim=-1) * weights).sum(dim=-1)
            for params in zip(*params_list)
        ]
        params_shape = [(param.numel(), param.shape) for param in avg_params]
        flatten_global_params = vectorize(self.global_model_params)

        for i, client_params in enumerate(params_list):
            self.nabla[i] += vectorize(client_params) - flatten_global_params

        flatten_new_params = vectorize(avg_params) + torch.stack(self.nabla).mean(dim=0)

        # reshape
        new_params = []
        i = 0
        for numel, shape in params_shape:
            new_params.append(flatten_new_params[i : i + numel].reshape(shape))
            i += numel
        self.global_model_params = OrderedDict(
            zip(self.global_model_params.keys(), new_params)
        )
