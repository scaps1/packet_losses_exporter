#!/usr/bin/python3
import subprocess
import time
import glob
import ipcalc
import re
import argparse

from prometheus_client import Gauge, start_http_server
from threading import Thread
from queue import Queue
from ipaddress import IPv4Network


class Config:
    RE_GRE_INTERFACE_NAME = r"(?<=auto )(gre[-0-9]+)"
    RE_LOCAL_GRE_IP = r"(?<=address )(\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)"
    RE_NETMASK = r"(?<=netmask )(\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)"
    RE_REMOTE_GRE_HOSTNAME = r"(?<=# remote: )(.*?)\n"
    RE_REMOTE_GRE_IP = r"(?<=remote )(\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)"

    def __init__(
        self,
        config_path=None
    ):
        self.config_path = config_path

    def read_cfg(self):
        unprocessed_files = []
        gre_tunnels = []

        for configs in glob.iglob(self.config_path + 'gre*.conf'):
            with open(configs, 'r', encoding='utf-8') as file:
                gre_conf = file.readlines()

            data = {
                'remote_gre_ip': None,
                'local_gre_ip': None,
                'gre_netmask': None,
                'gre_interface_name': None,
                'remote_gre_hostname': None
            }

            for line in gre_conf:
                result_remote_gre_ip = re.findall(self.RE_REMOTE_GRE_IP, line)
                result_gre_interface_name = re.findall(self.RE_GRE_INTERFACE_NAME, line)
                result_gre_host = re.findall(self.RE_REMOTE_GRE_HOSTNAME, line)
                result_local_ip = re.findall(self.RE_LOCAL_GRE_IP, line)
                result_gre_netmask = re.findall(self.RE_NETMASK, line)

                if result_remote_gre_ip:
                    data['remote_gre_ip'] = result_remote_gre_ip[0]
                if result_gre_interface_name:
                    data['gre_interface_name'] = result_gre_interface_name[0]
                if result_gre_host:
                    data['remote_gre_hostname'] = result_gre_host[0]
                if result_local_ip:
                    data['local_gre_ip'] = result_local_ip[0]
                if result_gre_netmask:
                    data['gre_netmask'] = result_gre_netmask[0]

            brokenconfig = False
            for key in data:
                if data[key] is None:
                    brokenconfig = True
                    break
            if brokenconfig:
                unprocessed_files.append(configs)
                continue

            CIDR = IPv4Network('0.0.0.0/' + data['gre_netmask']).prefixlen
            for x in ipcalc.Network(str(data['local_gre_ip']) + '/' + str(CIDR)):
                if data['local_gre_ip'] != str(x):
                    data['internal_dest_gre_ip'] = str(x)
            gre_tunnels.append(data)
            data.clear()

        return gre_tunnels, unprocessed_files


class Exporter:
    def __init__(
        self,
        config_path=None,
        polling_interval_seconds=None,
        max_workers=None
    ):
        self.outside = Gauge('outside_state', 'state', ["gre_interface_name", "local_gre_ip", "remote_gre_ip", "remote_gre_hostname"])
        self.inside = Gauge('inside_state', 'state', ["gre_interface_name", "local_gre_ip", "internal_dest_gre_ip", "remote_gre_hostname"])
        self.unprocessed_files_metric = Gauge('unprocessed_files', 'Count of unprocessed files', ['value'])
        self.queue = Queue()
        self.polling_interval_seconds = polling_interval_seconds
        self.config_path = config_path
        self.max_workers = max_workers

    def run_worker(self):
        while True:
            if self.queue.empty():  # Задач нет? Тогда немного поспим...
                time.sleep(1)
                continue
            q = self.queue.get()
            gre_interface_name = q['gre_interface_name']
            local_gre_ip = q['local_gre_ip']
            remote_gre_ip = q['remote_gre_ip']
            remote_gre_hostname = q['remote_gre_hostname']
            internal_dest_gre_ip = q['internal_dest_gre_ip']

            packetloss_outside = subprocess.run(["ping", "-c", "5", remote_gre_ip], capture_output=True, text=True)
            packetloss_outside = subprocess.run(["awk", "/packet loss/{gsub(/%/, \"\", $7); print $7}"], input=packetloss_outside.stdout, capture_output=True, text=True)
            packetloss_outside = packetloss_outside.stdout.strip()

            packetloss_inside = subprocess.run(["ping", "-c", "5", internal_dest_gre_ip], capture_output=True, text=True)
            packetloss_inside = subprocess.run(["awk", "/packet loss/{gsub(/%/, \"\", $7); print $7}"], input=packetloss_inside.stdout, capture_output=True, text=True)
            packetloss_inside = packetloss_inside.stdout.strip()

            self.outside.labels(gre_interface_name, local_gre_ip, remote_gre_ip, remote_gre_hostname).set(int(packetloss_outside))
            self.inside.labels(gre_interface_name, local_gre_ip, internal_dest_gre_ip, remote_gre_hostname).set(int(packetloss_inside))
            self.queue.task_done()

    def run_metrics_loop(self):
        threads = []

        for _ in range(self.max_workers):  # Создаем заданное количество потоков
            worker = Thread(target=self.run_worker)
            worker.setDaemon(True)
            worker.start()
            threads.append(worker)

        while True:
            metrics, unprocessed_files = Config(config_path=self.config_path).read_cfg()
            self.unprocessed_files_metric.labels(str(unprocessed_files)).set(len(unprocessed_files))

            for host in metrics:
                self.queue.put(host)

            time.sleep(self.polling_interval_seconds)

            # Очистка очереди после обработки
            while not self.queue.empty():
                self.queue.get()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config_path', help='path to config', default='/etc/network/interfaces.d/')
    parser.add_argument('--polling_interval_seconds', help='how often will be polled metrics', default=5)
    parser.add_argument('--exporter_port', help='metrics port', default=9698)
    parser.add_argument('--max_workers', help='max count of threads for exporter', default=9)
    args = parser.parse_args()
    losses_metrics = Exporter(
        config_path=args.config_path,
        polling_interval_seconds=args.polling_interval_seconds,
        max_workers=args.max_workers
    )

    start_http_server(args.exporter_port)
    losses_metrics.run_metrics_loop()


if __name__ == "__main__":
    main()
