FROM python:3.11.9-alpine As compile-image

WORKDIR /app

COPY requirements.txt /app/

RUN apk add --no-cache --virtual .build-deps gcc musl-dev postgresql-dev \
    && pip install --trusted-host pypi.python.org -r requirements.txt \
    && apk del .build-deps && rm -rf requirements.txt

RUN apk add --no-cache rclone libpq

FROM python:3.11.9-alpine As runtime-image

WORKDIR /app

RUN apk add --no-cache libpq

COPY --from=compile-image /usr/bin/rclone /usr/bin/rclone
COPY --from=compile-image /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

COPY config.yaml data.yaml setup.py media_downloader.py /app/
COPY module /app/module
COPY utils /app/utils

CMD ["python", "media_downloader.py"]
