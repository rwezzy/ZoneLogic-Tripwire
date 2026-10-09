FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY zonelogic ./zonelogic
COPY run.py .
ENV HOST=0.0.0.0 PORT=8080 ZONELOGIC_DATA_DIR=/data
RUN useradd --uid 10001 --create-home zonelogic && mkdir /data && chown zonelogic:zonelogic /data /app
USER zonelogic
EXPOSE 8080
CMD ["python", "run.py"]
