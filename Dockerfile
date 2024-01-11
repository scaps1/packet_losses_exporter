FROM python:3.9.5-alpine3.13

ADD ["main.py", "requirements.txt", "/opt/packet_losses_exporter/"]

WORKDIR /opt/packet_losses_exporter

RUN pip install -r requirements.txt

EXPOSE 9698

ENTRYPOINT [ "python3", "/opt/packet_losses_exporter/main.py" ]
