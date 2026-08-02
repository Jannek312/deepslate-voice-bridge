# Plain upstream Python base (Debian slim, multi-arch: amd64/aarch64). The
# bridge needs nothing from the Home Assistant base images; Supervisor's
# BUILD_FROM build-arg is intentionally unused (build.yaml is deprecated).
FROM docker.io/library/python:3.12-slim

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
