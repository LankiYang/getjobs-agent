# Runner image: non-headless Chrome on Xvfb (server deployment).
# Build from repo root after `vendor/get_jobs/gradlew bootJar`:
#   docker build -f deploy/runner/Dockerfile -t getjobs-runner:latest .

FROM eclipse-temurin:21-jdk-jammy

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11-utils x11vnc fonts-noto-cjk fontconfig \
        ca-certificates curl gnupg unzip \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Real Google Chrome: get_jobs launches channel("chrome"), not bundled Chromium.
RUN curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -f /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/get_jobs/build/patchright-driver /opt/patchright-driver
COPY vendor/get_jobs/build/libs/get_jobs-2.0.1.jar /opt/getjobs/get_jobs.jar
COPY deploy/runner/run-runner.sh /usr/local/bin/run-runner
RUN chmod +x /usr/local/bin/run-runner

ENV PLAYWRIGHT_CLI_DIR=/opt/patchright-driver
ENV GETJOBS_JAR=/opt/getjobs/get_jobs.jar

VOLUME ["/data"]
EXPOSE 19100

ENTRYPOINT ["/usr/local/bin/run-runner"]
CMD ["19100", "/data", "/opt/getjobs/get_jobs.jar"]