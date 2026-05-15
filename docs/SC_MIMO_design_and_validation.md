# SC-MIMO 文档索引

本文档原来同时记录 SC-MIMO 原理、仿真实现设计、开发计划和验证记录。为了避免理解说明和执行计划混在一起，内容已经拆分为两个独立文档。

- [SC_MIMO_understanding_and_simulation.md](SC_MIMO_understanding_and_simulation.md)：记录对 Qualcomm R1-2604697 中 SC-MIMO 方案的理解，以及本项目如何基于现有码字映射链路层仿真平台实现和复现实验。
- [SC_MIMO_implementation_plan.md](SC_MIMO_implementation_plan.md)：记录分阶段开发任务、每阶段目标、调试通过标志和当前验证记录。

本项目目标是在尽可能复用现有链路级仿真代码的前提下，复现并研究 Qualcomm SC-MIMO 的 CB-level staggered mapping 与 SIC 接收收益。
