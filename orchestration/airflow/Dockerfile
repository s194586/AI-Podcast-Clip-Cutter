FROM apache/airflow:3.3.0-python3.12

ARG AIRFLOW_VERSION=3.3.0
ARG PYTHON_VERSION=3.12
ARG AIRFLOW_CONSTRAINT_URL=https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o airflow -g root /opt/ai-cutter /opt/airflow/logs /opt/airflow/secrets

USER airflow
COPY --chown=airflow:root requirements.txt /tmp/podcast-cutter-requirements.txt
RUN sed 's/\r$//' /tmp/podcast-cutter-requirements.txt \
    | grep -vx "google-generativeai" \
    > /tmp/podcast-cutter-airflow-requirements.txt
RUN pip install --no-cache-dir \
    "apache-airflow==${AIRFLOW_VERSION}" \
    -r /tmp/podcast-cutter-airflow-requirements.txt \
    --constraint "${AIRFLOW_CONSTRAINT_URL}" \
    --trusted-host pypi.org \
    --trusted-host files.pythonhosted.org \
    --trusted-host raw.githubusercontent.com \
    && pip check

COPY --chown=airflow:root . /opt/ai-cutter
WORKDIR /opt/ai-cutter

ENV PYTHONPATH=/opt/ai-cutter \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
