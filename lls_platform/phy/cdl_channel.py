from __future__ import annotations

"""CDL/OFDM channel generation utilities for v2.6.

This module wraps Sionna PHY TR38.901 CDL and converts Sionna's native channel
shape to the platform-internal shape:

    H[batch, time, freq, rx_ant, tx_ant]

Sionna shapes used here:
    CDL CIR a: [batch, rx, rx_ant, tx, tx_ant, path, time]
    OFDM h_f: [batch, rx, rx_ant, tx, tx_ant, time, freq]
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np

from lls_platform.core.config import AntennaConfig, ChannelConfig, ResourceConfig
from lls_platform.phy.antenna_layout import SionnaTR38901PortOrder, make_simple_port_table


def _import_sionna_cdl():
    try:
        import tensorflow as tf
        from sionna.phy.channel.tr38901 import AntennaArray, CDL
        from sionna.phy.channel import subcarrier_frequencies, cir_to_ofdm_channel
        return tf, AntennaArray, CDL, subcarrier_frequencies, cir_to_ofdm_channel
    except Exception as e:
        raise ImportError(
            "CDL backend requires Sionna PHY channel.tr38901 modules. Original error: " + repr(e)
        )


@dataclass
class CDLChannelBatch:
    h_tf: object  # tf.Tensor [B,T,F,Rx,Tx]
    singular_values: np.ndarray  # [B,T,F,min(Rx,Tx)]
    report: dict


class CDLChannelBuilder:
    def __init__(self, antenna_cfg: AntennaConfig, resource_cfg: ResourceConfig, channel_cfg: ChannelConfig):
        self.antenna_cfg = antenna_cfg
        self.resource_cfg = resource_cfg
        self.channel_cfg = channel_cfg
        self.tf, self.AntennaArray, self.CDL, self.subcarrier_frequencies, self.cir_to_ofdm_channel = _import_sionna_cdl()
        self.bs_array = self._make_array(antenna_cfg.bs_antenna_array, is_bs=True)
        self.ut_array = self._make_array(antenna_cfg.ue_antenna_array, is_bs=False)
        cdl_type = str(getattr(channel_cfg, "cdl_type", "A")).replace("CDL-", "").replace("cdl-", "").upper()
        delay_spread = float(channel_cfg.delay_spread_ns) * 1e-9
        fc = float(resource_cfg.carrier_frequency_ghz) * 1e9
        speed = float(channel_cfg.ue_speed_kmh) / 3.6
        direction = str(getattr(channel_cfg, "direction", "downlink")).lower()
        self.cdl = self.CDL(
            model=cdl_type,
            delay_spread=delay_spread,
            carrier_frequency=fc,
            ut_array=self.ut_array,
            bs_array=self.bs_array,
            direction=direction,
            min_speed=speed,
            max_speed=speed,
        )

    def _make_array(self, arr, is_bs: bool):
        rows, cols, pols = [int(x) for x in arr]
        pol = "dual" if pols == 2 else "single"
        pol_type = str(self.antenna_cfg.polarization)
        if pol == "single":
            pol_type = "V" if pol_type not in ("H", "h") else "H"
        else:
            pol_type = "cross" if pol_type.lower() == "cross" else "VH"
        return self.AntennaArray(
            num_rows=rows,
            num_cols=cols,
            polarization=pol,
            polarization_type=pol_type,
            antenna_pattern=str(self.antenna_cfg.antenna_element_pattern),
            carrier_frequency=float(self.resource_cfg.carrier_frequency_ghz) * 1e9,
        )

    def sample(self, batch_size: int, fft_size: Optional[int] = None) -> CDLChannelBatch:
        tf = self.tf
        n_time = int(getattr(self.channel_cfg, "num_time_samples", self.resource_cfg.pdsch_n_symbols))
        n_freq = int(self.resource_cfg.n_prbs) * 12 if fft_size is None else int(fft_size)
        # For OFDM sampling, use total occupied bandwidth n_freq * SCS.
        sampling_frequency = float(n_freq) * float(self.resource_cfg.scs_khz) * 1e3
        a, tau = self.cdl(int(batch_size), n_time, sampling_frequency)
        freqs = self.subcarrier_frequencies(n_freq, float(self.resource_cfg.scs_khz) * 1e3)
        h_f = self.cir_to_ofdm_channel(freqs, a, tau, normalize=bool(self.channel_cfg.normalize))
        # Sionna: [B,Rx,RxAnt,Tx,TxAnt,T,F]. CDL only has one Tx and one Rx.
        h_tf = tf.transpose(h_f[:, 0, :, 0, :, :, :], [0, 3, 4, 1, 2])
        # Internal: [B,T,F,RxAnt,TxAnt]
        s = tf.linalg.svd(h_tf, compute_uv=False)
        report = self.report()
        report.update({
            "h_tf_shape": [int(x) for x in h_tf.shape],
            "sionna_h_f_shape": [int(x) if x is not None else -1 for x in h_f.shape],
        })
        s_np = np.sort(np.asarray(s.numpy()), axis=-1)[..., ::-1]
        return CDLChannelBatch(h_tf=h_tf, singular_values=s_np, report=report)

    def report(self) -> dict:
        bs_rows, bs_cols, bs_pols = [int(x) for x in self.antenna_cfg.bs_antenna_array]
        ue_rows, ue_cols, ue_pols = [int(x) for x in self.antenna_cfg.ue_antenna_array]
        return {
            "sionna_port_order": SionnaTR38901PortOrder().to_dict(),
            "bs_array": list(map(int, self.antenna_cfg.bs_antenna_array)),
            "ue_array": list(map(int, self.antenna_cfg.ue_antenna_array)),
            "bs_port_table": make_simple_port_table(bs_rows, bs_cols, bs_pols),
            "ue_port_table": make_simple_port_table(ue_rows, ue_cols, ue_pols),
            "cdl_type": str(getattr(self.channel_cfg, "cdl_type", "A")),
            "direction": str(getattr(self.channel_cfg, "direction", "downlink")),
            "delay_spread_ns": float(self.channel_cfg.delay_spread_ns),
            "ue_speed_kmh": float(self.channel_cfg.ue_speed_kmh),
        }
