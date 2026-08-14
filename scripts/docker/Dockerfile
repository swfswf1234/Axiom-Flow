# Axiom-Flow MinerU 镜像（基于官方 docker/global/Dockerfile，模型源改为 modelscope 适配国内网络）
# 参考：https://opendatalab.github.io/MinerU/quick_start/docker_deployment/
# 基础镜像 vLLM 0.21.0 / CUDA 13.0（主机驱动需支持 CUDA 13，见 ops 指南）
FROM vllm/vllm-openai:v0.21.0

# Install libgl for opencv support & Noto fonts for Chinese characters
RUN apt-get update && \
    apt-get install -y \
        fonts-noto-core \
        fonts-noto-cjk \
        fontconfig \
        libgl1 && \
    fc-cache -fv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install mineru latest
RUN python3 -m pip install -U 'mineru[core]>=3.4.0' --break-system-packages && \
    python3 -m pip cache purge

# Download models and update the configuration file（modelscope 源，国内网络）
RUN /bin/bash -c "mineru-models-download -s modelscope -m all"

# Set the entry point to activate the virtual environment and run the command line tool
ENTRYPOINT ["/bin/bash", "-c", "export MINERU_MODEL_SOURCE=local && exec \"$@\"", "--"]