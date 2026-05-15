from __future__ import annotations

"""Sionna TR38.901 antenna port-order helpers.

For Sionna PHY TR38.901 AntennaArray/PanelArray, the native port order for a
simple dual-polarized 2D AntennaArray(num_rows, num_cols) is:

    port = pol * num_rows * num_cols + col * num_rows + row

That is: polarization blocks first (all pol1 spatial ports, then all pol2), and
column-first spatial order within each polarization block. This follows the
Sionna antenna.py implementation that fills ant_pos[i + j*num_rows] and appends
pol2 positions after pol1 positions.
"""

from dataclasses import dataclass, asdict
from typing import Dict, List


@dataclass(frozen=True)
class SionnaTR38901PortOrder:
    spatial_order: str = "column_first_col_then_row"
    polarization_order: str = "pol1_all_spatial_then_pol2_all_spatial"
    simple_formula: str = "port = pol*num_rows*num_cols + col*num_rows + row"
    pol1_cross: str = "-45deg"
    pol2_cross: str = "+45deg"
    pol1_vh: str = "V"
    pol2_vh: str = "H"

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def sionna_tr38901_port_index(row: int, col: int, pol: int, num_rows: int, num_cols: int) -> int:
    """Return Sionna native port index for simple AntennaArray."""
    row = int(row); col = int(col); pol = int(pol)
    num_rows = int(num_rows); num_cols = int(num_cols)
    if not (0 <= row < num_rows):
        raise ValueError(f"row={row} out of [0,{num_rows})")
    if not (0 <= col < num_cols):
        raise ValueError(f"col={col} out of [0,{num_cols})")
    if pol not in (0, 1):
        raise ValueError("pol must be 0 or 1 for dual polarization")
    return pol * num_rows * num_cols + col * num_rows + row


def make_simple_port_table(num_rows: int, num_cols: int, num_pols: int) -> List[Dict[str, int]]:
    out = []
    for pol in range(int(num_pols)):
        for col in range(int(num_cols)):
            for row in range(int(num_rows)):
                port = pol * int(num_rows) * int(num_cols) + col * int(num_rows) + row
                out.append({"port": port, "row": row, "col": col, "pol": pol})
    return out
