FROM python:3.11.9-alpine As compile-image

RUN apk add --no-cache git git-lfs

# 动态时间戳破坏 Docker 镜像层缓存，确保每次部署都真正从 ModelScope 重新拉取最新代码
RUN echo "Deploy cache bust: 20261010-121500" \
    && rm -rf /src \
    && git clone --depth 1 https://www.modelscope.ai/studios/u09f26e1c/telegram-media-downloader.git /src \
    && cd /src && git lfs pull

WORKDIR /src

RUN apk add --no-cache --virtual .build-deps gcc musl-dev postgresql-dev \
    && pip install --trusted-host pypi.python.org -r /src/requirements.txt \
    && apk del .build-deps

RUN apk add --no-cache rclone libpq

FROM python:3.11.9-alpine As runtime-image

WORKDIR /app

ENV WEB_PORT=7860
EXPOSE 7860

RUN apk add --no-cache libpq

COPY --from=compile-image /usr/bin/rclone /usr/bin/rclone
COPY --from=compile-image /usr/bin/rclone /app/rclone/rclone
COPY --from=compile-image /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

COPY --from=compile-image /src/setup.py /src/app_main.py /src/media_downloader.py /app/
COPY --from=compile-image /src/module /app/module
COPY --from=compile-image /src/utils /app/utils

CMD ["python", "app_main.py"]
