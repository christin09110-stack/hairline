# Hairline. Two stages, so the runtime image carries no build tools.
#
# Built for **linux/arm64**. This is the Graviton product: it runs on an AWS
# Graviton EC2 instance rather than App Runner, because the Cloud Optimized OpenCV
# Library is an AMI and only exists on Arm. The stock aarch64 opencv wheel already
# carries Arm's KleidiCV HAL, which the /version endpoint prints, and
# docs/cool-benchmark.md is careful about what that means.
#
# Build from the REPO ROOT so the shared packages are in the context:
#   infra/ecr.sh hairline --context . --dockerfile products/hairline/Dockerfile --arch linux/arm64

FROM python:3.13-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY constraints.txt ./
COPY packages/visioncore ./packages/visioncore
COPY packages/servicekit ./packages/servicekit
COPY products/hairline ./products/hairline

RUN pip install --constraint constraints.txt \
      ./packages/visioncore ./packages/servicekit ./products/hairline \
 && python -c "import cv2, sys; v=cv2.__version__; sys.exit(0 if v.startswith('5.') else f'got OpenCV {v}, need 5.x')"

# ---------------------------------------------------------------------------

FROM python:3.13-slim-bookworm AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    OPENCV26_UPLOAD_DIR=/tmp/uploads \
    OPENCV26_MAX_CONCURRENT_JOBS=1

ARG GIT_SHA=unknown
ENV OPENCV26_GIT_SHA=${GIT_SHA}

RUN useradd --create-home --uid 10001 app
USER app
WORKDIR /home/app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["uvicorn", "hairline.service:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
