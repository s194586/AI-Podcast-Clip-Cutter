FROM apache/airflow:3.3.0-python3.12

ARG AIRFLOW_VERSION=3.3.0
ARG PYTHON_VERSION=3.12
ARG AIRFLOW_CONSTRAINT_URL=https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates ffmpeg \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o airflow -g root /opt/ai-cutter /opt/airflow/logs /opt/airflow/secrets

COPY --chown=root:root orchestration/airflow/bootstrap_ca_certificates.sh /usr/local/bin/bootstrap-ca-certificates
RUN chmod 0755 /usr/local/bin/bootstrap-ca-certificates

USER airflow
COPY --chown=airflow:root requirements.txt /tmp/podcast-cutter-requirements.txt
RUN sed 's/\r$//' /tmp/podcast-cutter-requirements.txt \
    | grep -Ev "^google-(generativeai|genai)([=<>!~].*)?$" \
    > /tmp/podcast-cutter-airflow-requirements.txt
RUN --mount=type=secret,id=custom_ca,required=false,uid=50000,gid=0,mode=0444 \
    set -eu; \
    build_ca_bundle=""; \
    if [ -f /run/secrets/custom_ca ]; then \
        openssl x509 -in /run/secrets/custom_ca -noout -checkend 0 >/dev/null 2>&1; \
        build_ca_bundle="/tmp/podcast-cutter-build-ca.pem"; \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/custom_ca > "${build_ca_bundle}"; \
        export PIP_CERT="${build_ca_bundle}" SSL_CERT_FILE="${build_ca_bundle}" REQUESTS_CA_BUNDLE="${build_ca_bundle}"; \
    fi; \
    pip install --no-cache-dir \
        "apache-airflow==${AIRFLOW_VERSION}" \
        -r /tmp/podcast-cutter-airflow-requirements.txt \
        --constraint "${AIRFLOW_CONSTRAINT_URL}"; \
    pip uninstall --yes apache-airflow-providers-google google-cloud-aiplatform; \
    pip install --no-cache-dir "google-genai==2.11.0"; \
    pip check; \
    if [ -n "${build_ca_bundle}" ]; then rm -f "${build_ca_bundle}"; fi

COPY --chown=airflow:root . /opt/ai-cutter
WORKDIR /opt/ai-cutter

ENV PYTHONPATH=/opt/ai-cutter \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER root
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/usr/local/bin/bootstrap-ca-certificates"]
