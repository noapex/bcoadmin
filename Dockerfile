FROM python:3.12

RUN python -m pip install --upgrade pip

COPY requirements.txt requirements.txt
RUN python -m pip install -r requirements.txt
RUN apt-get update && \
    apt-get install -y locales && \
    sed -i -e 's/# es_AR.UTF-8 UTF-8/es_AR.UTF-8 UTF-8/' /etc/locale.gen && \
    dpkg-reconfigure --frontend=noninteractive locales

# Debian's chromium ships its matching chromedriver and builds on amd64 and arm64;
# tzdata-legacy keeps the America/Buenos_Aires alias used by TIME_ZONE
RUN apt-get install -y chromium chromium-driver tzdata-legacy

# set display port to avoid crash
ENV DISPLAY=:99

ENV LANG es_AR.UTF-8
ENV LC_ALL es_AR.UTF-8
WORKDIR /usr/src/app
