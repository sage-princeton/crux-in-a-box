import torch
import gin 
import torch.nn as nn

@gin.configurable
def contrastive_loss(psi_0 , psi_T,  distance_fun, normalize=False, tau=None, exclude_diagonal = False, eps=10e-8, loss_type='backward'):
        if normalize:
            if distance_fun in ['l2', 'l22', 'dot']:
                psi_0 = nn.functional.normalize(psi_0, p=2, dim=1)
                psi_T = nn.functional.normalize(psi_T, p=2, dim=1)
            elif distance_fun == 'l1':
                psi_0 = nn.functional.normalize(psi_0, p=1, dim=1)
                psi_T = nn.functional.normalize(psi_T, p=1, dim=1)

        if tau is None:
            tau = 1 / (psi_0.shape[1] ** 0.5)
            

        l2 = (torch.mean(psi_T**2) + torch.mean(psi_0**2)) / 2
        I = torch.eye(psi_0.shape[0], device=psi_0.device)

        if distance_fun == 'l2':
            l_align = torch.sum((psi_T - psi_0)**2, dim=1) * tau
            pdist = torch.sum((psi_T[:, None] - psi_0[None])**2, dim=-1) * tau / psi_T.shape[-1]

            accuracy = torch.sum(torch.argmin(pdist, dim=1) == torch.arange(psi_0.shape[0], device=psi_0.device)) / psi_0.shape[0]
            if exclude_diagonal:
                 pdist = pdist * (1 - I)

        elif distance_fun == 'dot':    
            l_align = -torch.matmul(psi_T, psi_0.T)[torch.eye(psi_T.shape[0]).bool()] * tau
            pdist = -torch.sum(psi_T[:, None] * psi_0[None], dim=-1) * tau / psi_T.shape[-1]
            accuracy = torch.sum(torch.argmin(pdist, dim=1) == torch.arange(psi_0.shape[0], device=psi_0.device)) / psi_0.shape[0]
            if exclude_diagonal:
                 pdist = pdist * (1 - I)
        
        elif distance_fun == 'l1':
            l_align = torch.sum(torch.abs(psi_T - psi_0), dim=1) * tau
            pdist = torch.sum((psi_T[:, None] - psi_0[None])**2, dim=-1) * tau / psi_T.shape[-1]
            
            accuracy = torch.sum(torch.argmin(pdist, dim=1) == torch.arange(psi_0.shape[0], device=psi_0.device)) / psi_0.shape[0]
            if exclude_diagonal:
                 pdist = pdist * (1 - I)
                 
        elif distance_fun == 'l22':
            l_align = ((torch.sum((psi_T - psi_0)**2, dim=1) + eps)**0.5) * tau
            pdist = ((torch.sum((psi_T[:, None] - psi_0[None])**2, dim=-1) + eps)**0.5) * tau / psi_T.shape[-1]
            accuracy = torch.sum(torch.argmin(pdist, dim=1) == torch.arange(psi_0.shape[0], device=psi_0.device)) / psi_0.shape[0]
            if exclude_diagonal:
                 pdist = pdist * (1 - I)
        
        else: 
             raise ValueError()

        if loss_type == 'symmetric':
            l_unif = (torch.logsumexp(-pdist, dim=1) + torch.logsumexp(-pdist.T, dim=1)) / 2.0
        elif loss_type == 'forward':
            l_unif = torch.logsumexp(-pdist, dim=1) 
        elif loss_type == 'backward':
            l_unif = torch.logsumexp(-pdist.T, dim=1) 
        else:
            raise ValueError()
            

        

        loss = l_align + psi_T.shape[-1] * l_unif

        loss = loss.mean()

        metrics = {
                    "l_unif": l_unif.mean(), 
                    "l_align": l_align.mean(),

                    "accuracy": accuracy,
                    "l2": l2,
                    "loss": loss
        }

        return loss, metrics