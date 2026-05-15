from __future__ import annotations

from collections import defaultdict
from typing import List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from lls_platform.core.data_structures import SNRSummary


def plot_bler(summaries: List[SNRSummary], path) -> None:
    """Plot CB-BLER vs SNR."""
    groups = defaultdict(list)
    for s in summaries:
        groups[s.scheme_label].append(s)

    plt.figure()
    for label, vals in groups.items():
        vals = sorted(vals, key=lambda x: x.snr_db)
        xs = [v.snr_db for v in vals]
        ys = [max(v.cb_bler, 1e-5) for v in vals]
        plt.semilogy(xs, ys, marker="o", label=label)

    plt.xlabel("SNR (dB)")
    plt.ylabel("CB-BLER")
    plt.grid(True, which="both")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()


def plot_throughput(summaries: List[SNRSummary], path) -> None:
    """Plot total time-frequency goodput spectral efficiency vs SNR."""
    groups = defaultdict(list)
    for s in summaries:
        groups[s.scheme_label].append(s)

    plt.figure()
    for label, vals in groups.items():
        vals = sorted(vals, key=lambda x: x.snr_db)
        xs = [v.snr_db for v in vals]
        ys = [v.goodput_se_tf for v in vals]
        plt.plot(xs, ys, marker="o", label=label)

    plt.xlabel("SNR (dB)")
    plt.ylabel("Goodput SE (bits/TF-RE)")
    plt.grid(True)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()
