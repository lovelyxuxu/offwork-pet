FROM python:3.12-slim
WORKDIR /app
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt && useradd --uid 10001 --create-home offwork
COPY server /app/server
COPY offwork_pet/__init__.py offwork_pet/packages.py offwork_pet/package_limits.py /app/offwork_pet/
RUN mkdir -p /data/resources /data/tmp && chown -R offwork:offwork /data
USER offwork
ENV PYTHONUNBUFFERED=1 TMPDIR=/data/tmp
EXPOSE 8765
CMD ["python", "-m", "server", "--host", "0.0.0.0", "--port", "8765"]
