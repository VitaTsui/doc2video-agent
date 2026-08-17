# Doc2Video Agent —— 把「系统级依赖」一次性封进镜像。
#
# ffmpeg 走 Python wheel 内置（见 pyproject 的 bundled extra），LibreOffice 无法
# 用包管理器内置到 Python 环境里，只能在镜像层安装——这就是容器方案存在的理由。
#
# 默认镜像用纯 ffmpeg 渲染器，不含 Node / Chromium。需要 Remotion 的完整镜头
# 表现力时，见文件末尾的说明。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    D2V_RENDERER=ffmpeg \
    D2V_STORAGE_DIR=/data \
    HOME=/tmp

# libreoffice-impress: PPT/PPTX 按原始样式渲染成图（含旧版 .ppt 解析）
# ffmpeg:              Debian 版带 drawtext 与 ffprobe；内置 wheel 的 Linux 构建
#                      没有 drawtext（烧不了字幕），系统版在 PATH 里会优先被选中
# fonts-noto-cjk:      没有中文字体时幻灯片和字幕会变成方块
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress \
        libreoffice-core \
        ffmpeg \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app

COPY pyproject.toml README.md ./
COPY doc2video ./doc2video
RUN pip install --no-cache-dir -e ".[bundled]"

# 首次转换会初始化 LibreOffice 用户配置（约 2~4 秒）；在构建期做掉，
# 让线上第一次请求不吃这份延迟。
RUN soffice --headless --terminate_after_init >/dev/null 2>&1 || true

VOLUME ["/data"]
EXPOSE 8400

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8400/health')"

CMD ["doc2video", "serve", "--host", "0.0.0.0", "--port", "8400"]

# ---------------------------------------------------------------------------
# 想在容器里用 Remotion（镜头表现力更好，但镜像会大 ~500MB）时，追加：
#
#   RUN apt-get update && apt-get install -y --no-install-recommends \
#           nodejs npm libnss3 libdbus-1-3 libatk1.0-0 libasound2 \
#           libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
#       && rm -rf /var/lib/apt/lists/*
#   COPY renderer ./renderer
#   RUN cd renderer && npm install && npx remotion browser ensure
#   ENV D2V_RENDERER=auto
# ---------------------------------------------------------------------------
