FROM python:3.12-alpine
WORKDIR /app
COPY *.py ./
COPY static ./static
ENV FLIPDESK_DB=/data/flipdesk.db FLIPDESK_PORT=8080
VOLUME /data
EXPOSE 8080
CMD ["python3", "server.py"]
