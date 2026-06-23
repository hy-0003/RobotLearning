# ⚙ My RobotLearning

**Personal Research & Engineering for Embodied AI**

[![GitHub repo](https://img.shields.io/badge/repo-RobotLearning-181717?style=flat-square&logo=github)](https://github.com/your-username/RobotLearning)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![Made with ❤️](https://img.shields.io/badge/Made%20with-❤️-ff69b4?style=flat-square)](https://github.com/your-username/RobotLearning)

[![Python](https://img.shields.io/badge/Python-3.10-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-EE4C2C?style=flat-square&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![Hugging Face](https://img.shields.io/badge/Hugging%20Face-LeRobot-ffbd2b?style=flat-square&logo=huggingface&logoColor=black)](https://huggingface.co/lerobot)
[![Windows](https://img.shields.io/badge/Platform-Windows%2011-0078D6?style=flat-square&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![NVIDIA](https://img.shields.io/badge/GPU-RTX%204060-76B900?style=flat-square&logo=nvidia&logoColor=white)](https://www.nvidia.com)


## Overview

This repository is my central hub for my robot learning.  
Each project lives in its own orphan branch – completely independent, fully documented, and ready to run.

*From simulation to real‑world, from VLA fine‑tuning to multi‑agent orchestration – this is where I build, break, and learn.*


## Projects

| Project | Branch | Tech Stack | Status |
|:--------|:-------|:-----------|:-------|
| **Running_SmolVLA** | [`runnig smolvla`](https://github.com/hy-0003/RobotLearning/tree/smolvla) | `PyTorch` · `LeRobot` · `LIBERO` · `Windows` | Complete |
| **MRSLLM** | [`multi-robot scheduler with LLM`](https://github.com/hy-0003/RobotLearning/tree/MRSLLM) | `LLM` · `Disjunctive Graph` · `Harness` · `MRTA` | Complete |
| *More coming soon...* | – | – | 🔜 Planning |


### 1. Running_SmolVLA

Independent reproduction of Hugging Face's **SmolVLA** – trained and evaluated natively on Windows 11 (RTX 4060).  

Overcame GPU OOM, path issues, and dependency hell to achieve **87%+ success rate** on LIBERO tasks.

[![SmolVLA](https://img.shields.io/badge/-View%20Project-181717?style=for-the-badge&logo=github)](https://github.com/hy-0003/RobotLearning/tree/smolvla)  
[![Paper](https://img.shields.io/badge/Paper-SmolVLA-ffbd2b?style=flat-square)](https://arxiv.org/abs/2506.01844) · [![Demo](https://img.shields.io/badge/Demo-Video-ff69b4?style=flat-square)](https://github.com/hy-0003/RobotLearning/blob/smolvla/task8_s.gif)

---

### 2. Multi‑Robot Scheduler with LLM

Closed‑loop system that combines LLM task planning, disjunctive graph scheduling, and simulation‑in‑the‑loop to solve MRTA‑Benchmark problems.  

[![Multi-Robot](https://img.shields.io/badge/-View%20Project-181717?style=for-the-badge&logo=github)](https://github.com/hy-0003/RobotLearning/tree/MRSLLM)  
[![IMR-LLM](https://img.shields.io/badge/Benchmark-MRTA-blue?style=flat-square)](https://arxiv.org/abs/2603.02669) · [![LLM](https://img.shields.io/badge/LLM-OpenAI%20%7C%20DeepSeek-412991?style=flat-square)](https://platform.deepseek.com/)

---


## ◆ How to Explore

This repository uses orphan branches – each branch contains a self‑contained project with its own README.md, code, and data.

```bash
# Clone the entire repo (all branches)
git clone https://github.com/hy-0003/RobotLearning.git
cd RobotLearning

# Switch to a specific project branch
git checkout smolvla      # for SmolVLA
git checkout MRSLLM       # for multi‑robot scheduler
```

## ■ About the Author
I'm a freshman in Embodied AI.

## License
Distributed under the MIT License. See LICENSE for more information.

<p align="center"> <sub>Built by Yi He</sub> </p>
