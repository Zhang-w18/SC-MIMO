"""Flexible codeword mapping link-level simulation platform.

v3.1 builds on v3.0 and adds per-CW OLLA:
- TX keeps ideal SVD precoding unless configured otherwise;
- RX-CE abstraction from v3.0 is retained;
- per-(SNR, scheme, CW) OLLA SINR offsets can be learned from CB feedback;
- warmup trials are excluded from final results, but retained for trace plots;
- postprocess_curves.py plots OLLA MCS/code-rate/offset/EWMA traces.
"""
__version__ = "3.1.0"
