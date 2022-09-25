#!/usr/bin/env python
# -*- coding:utf-8 -*-
# Author: leeyoshinari
import os
import re
import time
import json
import copy
import queue
import traceback
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
import influxdb
from common import handle_exception, get_ip
from logger import logger, cfg


class PerMon(object):
    def __init__(self):
        self.check_sysstat_version()
        self.IP = get_ip()
        self.room_id = None  # server room id
        self.group = None   # server group id
        self.thread_pool = 2
        self.is_system = 1        # Whether to monitor the server system
        self.error_times = cfg.getMonitor('errorTimes')
        self.maxCPU = cfg.getMonitor('maxCPU')
        self.CPUDuration = cfg.getMonitor('CPUDuration')
        self.isCPUAlert = cfg.getMonitor('isCPUAlert')
        self.minMem = cfg.getMonitor('minMem')
        self.isMemAlert = cfg.getMonitor('isMemAlert')
        self.frequencyFGC = cfg.getMonitor('frequencyFGC')
        self.isJvmAlert = cfg.getMonitor('isJvmAlert')
        self.echo = cfg.getMonitor('echo')
        self.isDiskAlert = cfg.getMonitor('isDiskAlert')
        self.maxDiskUsage = cfg.getMonitor('maxDiskUsage') / 100

        system_interval = cfg.getMonitor('systemInterval')
        self.system_interval = max(system_interval, 1)   # If the set value is less than 1, the default is 1
        self.system_interval = self.system_interval - 1.1      # Program running time
        self.system_interval = max(self.system_interval, 0)

        self.system_version = ''   # system version
        self.cpu_info = ''
        self.cpu_usage = 0.0    # CPU usage
        self.cpu_cores = 0      # number of CPU core
        self.mem_usage = 0.0    # memory usage
        self.total_mem = 0      # totel memory, unit: G
        self.total_mem_100 = 0  # total memory, unit: 100*G
        self.nic = ''           # network card
        self.all_disk = []      # disk number
        self.total_disk = 1     # total disk size, unit: M
        self.total_disk_h = 0     # total disk size, unit:T or G
        self.network_speed = cfg.getAgent('nicSpeed')  # bandwidth
        self.Retrans_num = self.get_RetransSegs()   # TCP retrans number
        self.java_info = {'status': 0, 'pid': '', 'port': '', 'port_status': 0}

        self.influx_host = '127.0.0.1'
        self.influx_port = 8086
        self.influx_username = 'root'
        self.influx_password = '123456'
        self.influx_database = 'test'

        self.get_system_version()
        self.get_cpu_cores()
        self.get_total_mem()
        self.get_system_nic()
        self.get_disks()
        self.get_system_net_speed()
        self.get_total_disk_size()
        self.get_java_info()

        self.get_config_from_server()
        self.monitor_task = queue.Queue()   # FIFO queue
        self.executor = ThreadPoolExecutor(self.thread_pool)
        self.client = influxdb.InfluxDBClient(self.influx_host, self.influx_port, self.influx_username,
                                              self.influx_password, self.influx_database)  # influxdb connection

        self.FGC = {}           # full gc times
        self.FGC_time = {}      # full gc time
        self.last_cpu_io = []   # recently cpu usage

        self.monitor()

    @property
    def start(self):
        return self.is_system

    @start.setter
    def start(self, value):
        self.is_system = value

    @property
    def stop(self):
        return self.is_system

    @stop.setter
    def stop(self, value):
        self.is_system = 0

    def get_config_from_server(self):
        url = f'http://{cfg.getServer("address")}/Register/first'
        header = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json; charset=UTF-8"}
        post_data = {
            'host': self.IP,
            'port': cfg.getAgent('port'),
            'system': self.system_version,
            'cpu': self.cpu_cores,
            'cpu_usage': self.cpu_usage,
            'nic': self.nic,
            'network_speed': self.network_speed,
            'mem': round(self.total_mem, 2),
            'mem_usage': self.mem_usage,
            'disk_size': self.total_disk_h,
            'disk_usage': self.get_used_disk_rate(),
            'disks': ','.join(self.all_disk)
        }

        while True:
            try:
                res = requests.post(url=url, json=post_data, headers=header)
                logger.info(f"The result of registration is {res.content.decode('unicode_escape')}")
                if res.status_code == 200:
                    response_data = json.loads(res.content.decode('unicode_escape'))
                    if response_data['code'] == 0:
                        self.influx_host = response_data['data']['host']
                        self.influx_port = response_data['data']['port']
                        self.influx_username = response_data['data']['username']
                        self.influx_password = response_data['data']['password']
                        self.influx_database = response_data['data']['database']
                        self.group = response_data['data']['group_identifier']
                        self.room_id = response_data['data']['room_id']
                        break
                    else:
                        logger.error(response_data['msg'])
                        raise Exception(response_data['msg'])

                time.sleep(1)

            except(Exception):
                logger.error(traceback.format_exc())
                time.sleep(1)

    def worker(self):
        """
        Get data from the queue and start monitoring
        :return:
        """
        while True:
            func, param = self.monitor_task.get()
            func(param)
            self.monitor_task.task_done()

    def monitor(self):
        """
        start monitoring
        :return:
        """
        for i in range(self.thread_pool):
            self.executor.submit(self.worker)

        # Put registration and cleanup tasks in the queue
        self.monitor_task.put((self.register_agent, True))
        # Put the tasks of the monitoring system into the queue
        self.monitor_task.put((self.write_system_cpu_mem, 1))

    def write_system_cpu_mem(self, is_system):
        """
        Monitoring system. CPU, Memory, Disk IO, Network, TCP
        :param is_system:
        :return:
        """
        cpu_flag = True     # Flag of whether to send mail when the CPU usage is too high
        mem_flag = True     # Flag of whether to send mail when the free memory is too low
        echo = True         # Flag of whether to clean up cache
        line = [{'measurement': self.room_id,
                 'tags': {'host': self.IP, 'group': self.group, 'type': 'system'},
                 'fields': {
                     'c_time': '',
                     'cpu': 0.0,
                     'iowait': 0.0,
                     'usr_cpu': 0.0,
                     'mem': 0.0,
                     'mem_available': 0.0,
                     'rec': 0.0,
                     'trans': 0.0,
                     'net': 0.0,
                     'tcp': 0,
                     'retrans': 0,
                     'port_tcp': 0,
                     'close_wait': 0,
                     'time_wait': 0,
                     'jvm': 0
                 }}]
        for disk in self.all_disk:
            # The system disks exists in the format of 'sda-1'. Since influxdb cannot recognize the '-', need to replace it.
            # Other formats need to be verified
            disk_n = disk.replace('-', '')
            line[0]['fields'].update({disk_n: 0.0})
            line[0]['fields'].update({disk_n + '_r': 0.0})
            line[0]['fields'].update({disk_n + '_w': 0.0})
            line[0]['fields'].update({disk_n + '_d': 0.0})

        while True:
            if self.is_system:
                try:
                    res = self.get_system_cpu_io_speed()   # get CPU, memory, IO, network, TCP

                    if res['disk'] and res['cpu'] is not None and res['mem'] is not None:
                        for k, v in res['disk'].items():
                            line[0]['fields'][k] = min(v, 100.0)

                        for k, v in res['disk_r'].items():
                            line[0]['fields'][k] = v

                        for k, v in res['disk_w'].items():
                            line[0]['fields'][k] = v

                        for k, v in res['disk_d'].items():
                            line[0]['fields'][k] = v

                        line[0]['fields']['cpu'] = res['cpu']
                        line[0]['fields']['iowait'] = res['iowait']
                        line[0]['fields']['usr_cpu'] = res['usr_cpu']
                        line[0]['fields']['mem'] = res['mem']
                        line[0]['fields']['mem_available'] = res['mem_available']
                        line[0]['fields']['rec'] = res['rece']
                        line[0]['fields']['trans'] = res['trans']
                        line[0]['fields']['net'] = res['network']
                        line[0]['fields']['tcp'] = res['tcp']
                        line[0]['fields']['retrans'] = res['retrans']
                        line[0]['fields']['port_tcp'] = res['port_tcp']
                        line[0]['fields']['close_wait'] = res['close_wait']
                        line[0]['fields']['time_wait'] = res['time_wait']
                        line[0]['fields']['jvm'] = res['jvm']
                        self.client.write_points(line)    # write to database
                        logger.info(f"system:{res}")

                        if len(self.last_cpu_io) > self.CPUDuration:
                            self.last_cpu_io.pop(0)

                        self.last_cpu_io.append(res['cpu'])
                        self.cpu_usage = sum(self.last_cpu_io) / len(self.last_cpu_io)     # CPU usage, with %
                        self.mem_usage = 1 - res['mem'] / self.total_mem    # Memory usage, without %

                        if self.cpu_usage > self.maxCPU:
                            msg = f'{self.IP} server CPU average usage is {self.cpu_usage}%, it is too high.'
                            logger.warning(msg)
                            if self.isCPUAlert and cpu_flag:
                                cpu_flag = False    # Set to False to prevent sending email continuously
                                thread = threading.Thread(target=notification, args=(msg,))
                                thread.start()
                        else:
                            cpu_flag = True     # If CPU usage is normally, reset it to True

                        if res['mem'] <= self.minMem:
                            msg = f"{self.IP} system free memory is {res['mem']}G, it is too low."
                            logger.warning(msg)
                            if self.isMemAlert and mem_flag:
                                mem_flag = False    # Set to False to prevent sending email continuously
                                thread = threading.Thread(target=notification, args=(msg, ))
                                thread.start()

                            if self.echo and echo:
                                echo = False    # Set to False to prevent cleaning up cache continuously
                                thread = threading.Thread(target=self.clear_cache, args=())
                                thread.start()

                        else:
                            mem_flag = True     # If free memory is normally, reset it to True.
                            echo = True

                except(Exception):
                    logger.error(traceback.format_exc())

                time.sleep(self.system_interval)
            else:
                time.sleep(3)

    @handle_exception(is_return=True, default_value=0)
    def get_jvm(self, port, pid):
        """
        JVM size
        :param port: port
        :param pid: pid
        :return: jvm(G)
        """
        try:
            result = os.popen(f'jstat -gc {pid}').readlines()[1]
        except IndexError:
            self.java_info['status'] = 0
            return 0
        res = result.strip().split()
        logger.debug(f'The JVM of pid {pid} is: {res}')
        mem = float(res[2]) + float(res[3]) + float(res[5]) + float(res[7])     # calculate JVM

        fgc = int(res[14])
        if self.FGC[str(port)] < fgc:  # If the times of FGC increases
            self.FGC[str(port)] = fgc
            self.FGC_time[str(port)].append(time.time())
            if len(self.FGC_time[str(port)]) > 2:   # Calculate FGC frequency
                frequency = self.FGC_time[str(port)][-1] - self.FGC_time[str(port)][-2]
                if frequency < self.frequencyFGC:    # If FGC frequency is too high, send email.
                    msg = f'The Full GC frequency of port {port} is {frequency}, it is too high. Server IP: {self.IP}'
                    logger.warning(msg)
                    if self.isJvmAlert:
                        thread = threading.Thread(target=notification, args=(msg, ))
                        thread.start()

            # Write FGC times and time to log
            logger.warning(f"The port {port} has Full GC {self.FGC[str(port)]} times.")

        elif self.FGC[str(port)] > fgc:   # If the times of FGC is reduced, the port may be restarted, then reset it
            self.FGC[str(port)] = 0

        if self.FGC[str(port)] == 0:    # If the times of FGC is 0, reset FGC time.
            self.FGC_time[str(port)] = []

        return mem / 1048576    # 1048576 = 1024 * 1024

    @handle_exception(is_return=True, default_value={})
    def get_system_cpu_io_speed(self):
        """
        Get system CPU usage, memory, disk IO, network speed, etc.
        :return:
        """
        disk = {}
        disk_r = {}
        disk_w = {}
        disk_d = {}
        cpu = None
        iowait = None
        usr_cpu = None
        bps1 = None
        bps2 = None
        rece = None
        trans = None
        network = None
        port_tcp = 0
        close_wait = 0
        time_wait = 0
        jvm = 0
        if self.nic:
            bps1 = os.popen(f'cat /proc/net/dev |grep {self.nic}').readlines()
            logger.debug(f'The result of speed for the first time is: {bps1}')

        result = os.popen('iostat -x -m 1 2').readlines()
        logger.debug(f'The result of Disks are: {result}')

        if self.nic:
            bps2 = os.popen(f'cat /proc/net/dev |grep {self.nic}').readlines()
            logger.debug(f'The result of speed for the second time is: {bps2}')

        result = result[len(result) // 2 - 1:]
        disk_res = [line.strip() for line in result if len(line) > 5]

        for i in range(len(disk_res)):
            if 'avg-cpu' in disk_res[i]:
                cpu_res = disk_res[i+1].strip().split()      # Free CPU
                cpu = 100 - float(cpu_res[-1])      # CPU usage
                iowait = float(cpu_res[-3])
                usr_cpu = float(cpu_res[0])
                logger.debug(f'System CPU usage rate is: {cpu}%')
                continue

            if 'Device' in disk_res[i]:
                for j in range(i+1, len(disk_res)):
                    disk_line = disk_res[j].split()
                    disk_num = disk_line[0].replace('-', '')
                    disk.update({disk_num: float(disk_line[-1])})      # IO
                    disk_r.update({disk_num + '_r': float(disk_line[2])})     # Read MB/s
                    disk_w.update({disk_num + '_w': float(disk_line[8])})     # Write MB/s
                    disk_d.update({disk_num + '_d': float(disk_line[14])})     # MB/s

                logger.debug(f'The result of disks are: IO: {disk}, Read: {disk_r}, Write: {disk_w}')

                break

        mem, mem_available = self.get_free_memory()
        if self.java_info['port_status'] == 1:
            tcp_num = self.get_port_tcp(self.java_info['port'], self.java_info['pid'])
            port_tcp = tcp_num.get('tcp', 0)
            close_wait = tcp_num.get('close_wait', 0)
            time_wait = tcp_num.get('time_wait', 0)
        if self.java_info['status'] == 1:
            jvm = self.get_jvm(self.java_info['port'], self.java_info['pid'])  # get JVM size

        if bps1 and bps2:
            data1 = bps1[0].split()
            data2 = bps2[0].split()
            rece = (int(data2[1]) - int(data1[1])) / 1048576
            trans = (int(data2[9]) - int(data1[9])) / 1048576
            # 400 = 8 * 100 / 2
            # Why multiply by 8, because 1MB/s = 8Mb/s.
            # Why divided by 2, because the network card is in full duplex mode.
            network = 400 * (rece + trans) / self.network_speed
            logger.debug(f'The bandwidth of ethernet is: Receive {rece}MB/s, Transmit {trans}MB/s, Ratio {network}%')

        tcp, Retrans = self.get_tcp()

        return {'disk': disk, 'disk_r': disk_r, 'disk_w': disk_w, 'disk_d': disk_d, 'cpu': cpu, 'iowait': iowait,
                'usr_cpu': usr_cpu, 'mem': mem, 'mem_available': mem_available, 'rece': rece, 'trans': trans,
                'network': network, 'tcp': tcp, 'retrans': Retrans, 'port_tcp': port_tcp, 'close_wait': close_wait,
                'time_wait': time_wait, 'jvm': jvm}

    @staticmethod
    def get_free_memory():
        """
        Get system memory
        :return: free Memory, available Memory
        """
        mem, mem_available = 0, 0
        result = os.popen('cat /proc/meminfo').readlines()
        logger.debug(f'The free memory is: {result}')
        for res in result:
            if 'MemFree' in res:
                mem = int(res.split(':')[-1].split('k')[0].strip()) / 1048576     # 1048576 = 1024 * 1024
                continue
            if 'MemAvailable' in res:
                mem_available = int(res.split(':')[-1].split('k')[0].strip()) / 1048576   # 1048576 = 1024 * 1024
                continue
            if mem and mem_available:
                break

        return mem, mem_available

    @handle_exception(is_return=True, default_value=(0, 0))
    def get_tcp(self):
        """
        Get the number of TCP and calculate the retransmission rate
        :return:
        """
        result = os.popen('cat /proc/net/snmp |grep Tcp').readlines()
        tcps = result[-1].split()
        logger.debug(f'The TCP is: {tcps}')
        tcp = int(tcps[9])      # TCP connections
        Retrans = int(tcps[-4]) - self.Retrans_num
        self.Retrans_num = int(tcps[-4])

        return tcp, Retrans

    @handle_exception(is_return=True, default_value={})
    def get_port_tcp(self, port, pid):
        """
        Get the number of TCP connections for the port
        :param port: port
        :return:
        """
        tcp_num = {}
        try:
            res = os.popen(f'netstat -ant |grep {port}').read()
            if res.count('LISTEN') == 0 and res.count(pid) == 0:
                self.java_info['port_status'] = 0
                self.java_info['status'] = 0
            tcp_num.update({'tcp': res.count('tcp')})
            tcp_num.update({'established': res.count('ESTABLISHED')})
            tcp_num.update({'close_wait': res.count('CLOSE_WAIT')})
            tcp_num.update({'time_wait': res.count('TIME_WAIT')})
        except Exception as err:
            logger.info(err)
        return tcp_num

    def get_cpu_cores(self):
        """
        Get CPU information
        :return:
        """
        cpu_model = None
        cpu_num = 0
        cpu_core = 0
        try:
            result = os.popen('cat /proc/cpuinfo | grep "model name" |uniq').readlines()[0]
            cpu_model = result.strip().split(':')[1].strip()
            logger.info(f'The CPU model is {cpu_model}')
        except Exception as err:
            logger.error('The CPU model is not found.')
            logger.error(err)

        try:
            result = os.popen('cat /proc/cpuinfo | grep "physical id" | uniq | wc -l').readlines()[0]
            cpu_num = int(result)
            logger.info(f'The number of CPU is {cpu_num}')
        except Exception as err:
            logger.error('The number of CPU is not found.')
            logger.error(err)

        try:
            result = os.popen('cat /proc/cpuinfo | grep "cpu cores" | uniq').readlines()[0]
            cpu_core = int(result.strip().split(':')[1].strip())
            logger.info(f'The number of cores per CPU is {cpu_core}')
        except Exception as err:
            logger.error('The number of cores per CPU is not found.')
            logger.error(err)

        result = os.popen('cat /proc/cpuinfo| grep "processor"| wc -l').readlines()[0]
        self.cpu_cores = int(result)
        logger.info(f'The number of cores all CPU is {self.cpu_cores}')

        if cpu_model and cpu_num and cpu_core:
            self.cpu_info = f'{cpu_num} CPU(s), {cpu_core} core(s) pre CPU, total {self.cpu_cores} cores, ' \
                            f'CPU model is {cpu_model} '
        elif cpu_model:
            self.cpu_info = f'total CPU cores is {self.cpu_cores}, CPU model is {cpu_model} '
        else:
            self.cpu_info = f'total CPU cores is {self.cpu_cores}'

    @handle_exception(is_return=True)
    def get_total_mem(self):
        """
        Get Memory
        :return:
        """
        result = os.popen('cat /proc/meminfo| grep "MemTotal"').readlines()[0]
        self.total_mem = float(result.split(':')[-1].split('k')[0].strip()) / 1048576   # 1048576 = 1024 * 1024
        self.total_mem_100 = self.total_mem / 100
        logger.info(f'The total memory is {self.total_mem}G')

    @handle_exception()
    def get_disks(self):
        """
        Get all disks number.
        :return:
        """
        result = os.popen('iostat -x -k').readlines()
        if result:
            disk_res = [line.strip() for line in result if len(line) > 5]
            for i in range(len(disk_res)):
                if 'Device' in disk_res[i]:
                    for j in range(i + 1, len(disk_res)):
                        disk_line = disk_res[j].split()
                        self.all_disk.append(disk_line[0])

            logger.info(f'The system has {len(self.all_disk)} disks, disk number is {"、".join(self.all_disk)}')
        else:
            raise Exception('The system does not support the iostat, please install sysstat. ')

    @handle_exception(is_return=True)
    def get_system_nic(self):
        """
        Get network card.
        Only one network card can be got. If the system uses multiple network cards, only the first one can
        be got. Use "cat /proc/net/dev" to view the order of the network cards.
        :return:
        """
        network_card = []
        result = os.popen('cat /proc/net/dev').readlines()   # get network data
        logger.debug(f'The result for the first time is: {result}')
        time.sleep(1)
        result1 = os.popen('cat /proc/net/dev').readlines()  # get network data again
        logger.debug(f'The result for the second time is: {result1}')
        for i in range(len(result)):
            if ':' in result[i]:
                data = result[i].split()
                data1 = result1[i].split()
                if data[0] == data1[0]:
                    logger.debug(f'The first data change is {data}')
                    logger.debug(f'The second data change is {data1}')
                    if data[1] != data1[1] or data[9] != data1[9]:     # If the data of network card changes, it means that the card is in use.
                        network_card.append(data[0].strip(':'))

        logger.debug(f'The data of network card is {network_card}')
        if 'lo' in network_card:    # 'lo' is 127.0.0.1, need to be deleted.
            network_card.pop(network_card.index('lo'))

        if len(network_card) > 0:
            self.nic = network_card[0]
            logger.info(f'The network card in use is {self.nic}')
        else:
            logger.error('The network card in use is not found.')

    @handle_exception(is_return=True)
    def get_total_disk_size(self):
        """
        Get disk size
        :return:
        """
        result = os.popen('df -m').readlines()
        logger.debug(f'The data of disk is {result}')
        for line in result:
            res = line.split()
            if '/dev/' in res[0]:
                size = float(res[1])
                self.total_disk += size
        logger.debug(f'The disks total size is {self.total_disk}M')

        self.total_disk_h = self.total_disk / 1024
        if self.total_disk_h > 1024:
            total = round(self.total_disk_h / 1024, 2)
            self.total_disk_h = f'{total}T'
        else:
            total = round(self.total_disk_h, 2)
            self.total_disk_h = f'{total}G'

        logger.info(f'The total size of disks is {self.total_disk_h}')

    @handle_exception(is_return=True, default_value=0)
    def get_used_disk_rate(self):
        """
        Get disks usage
        :return:
        """
        used_disk_size = 0
        result = os.popen('df -m').readlines()
        logger.debug(f'The data of disk is {result}')
        for line in result:
            res = line.split()
            if '/dev/' in res[0]:
                size = float(res[2])
                used_disk_size += size
        logger.info(f'The used size of disks is {used_disk_size}M')
        return used_disk_size / self.total_disk

    @handle_exception(is_return=True)
    def get_system_net_speed(self):
        """
        Get bandwidth, Mbs
        :return:
        """
        if self.nic:
            result = os.popen(f'ethtool {self.nic}').readlines()
            logger.debug(f'The bandwidth is {result}')
            for line in result:
                if 'Speed' in line:
                    logger.debug(f'The bandwidth is {line}')
                    res = re.findall(r"(\d+)", line)
                    speed = int(res[0])
                    if 'G' in line:
                        speed = speed * 1024
                    if 'K' in line:
                        speed = speed / 1024

                    self.network_speed = speed
                    break

            logger.info(f'The bandwidth of ethernet is {self.network_speed}Mb/s')

    @handle_exception(is_return=True)
    def get_system_version(self):
        """
        Get system version
        :return:
        """
        try:
            result = os.popen('cat /etc/redhat-release').readlines()    # system release version
            logger.debug(f'The system release version is {result}')
            self.system_version = result[0].strip()
        except Exception as err:
            logger.warning(err)
            result = os.popen('cat /proc/version').readlines()[0]   # system kernel version
            logger.debug(f'The system kernel version is{result}')
            res = re.findall(r"gcc.*\((.*?)\).*GCC", result.strip())
            if res:
                self.system_version = res[0]
            else:
                res = re.findall(r"gcc.*\((.*?)\)", result.strip())
                self.system_version = res[0]

        logger.info(f'system release/kernel version is {self.system_version}')

    @handle_exception(is_return=True, default_value=0)
    def get_RetransSegs(self):
        """
            Get the number of TCP RetransSegs
            :return:
        """
        result = os.popen('cat /proc/net/snmp |grep Tcp').readlines()
        tcps = result[-1].split()
        logger.debug(f'The TCP is: {tcps}')
        Retrans = int(tcps[-4])

        return Retrans

    def get_java_info(self):
        """
        Find java service
        """
        try:
            pid = os.popen("ps -ef|grep java |grep " + self.group + " |grep -v grep |awk '{print $1}'").readlines()[0]
            if pid.strip():
                self.java_info['pid'] = pid.strip()
                port = os.popen("netstat -antp|grep " + self.java_info['pid'] + " |grep LISTEN |tr -s ' ' |awk '{print $4}' | awk -F ':' '{print $2}'").readlines()[0]
                self.java_info['port'] = port.strip()
                self.java_info['port_status'] = 1
                try:
                    result = os.popen(f'jstat -gc {self.java_info["pid"]} |tr -s " "').readlines()[1]
                    res = result.strip().split(' ')
                    logger.info(f'The JVM of {pid} is {res}')
                    _ = float(res[2]) + float(res[3]) + float(res[5]) + float(res[7])
                    self.java_info['status'] = 1
                except Exception as err:
                    logger.warning(err)
                    self.java_info['status'] = 0
            else:
                self.java_info['status'] = 0
                self.java_info['port_status'] = 0

        except Exception as err:
            logger.warning(err)
            self.java_info['status'] = 0
            self.java_info['port_status'] = 0

    def check_sysstat_version(self):
        """
        Check sysstat version
        """
        try:
            version = os.popen("iostat -V |grep ysstat |awk '{print $3}' |awk -F '.' '{print $1}'").readlines()[0]
            v = int(version.strip())
            if v < 12:
                msg = 'The iostat version is too low, please upgrade to version 12+, download link: ' \
                      'http://sebastien.godard.pagesperso-orange.fr/download.html'
                logger.error(msg)
                raise Exception(msg)
        except IndexError:
            logger.error(traceback.format_exc())
            msg = 'Please install or upgrade sysstat to version 12+, download link: ' \
                  'http://sebastien.godard.pagesperso-orange.fr/download.html'
            logger.error(msg)
            raise Exception(msg)

        try:
            version = os.popen("pidstat -V |grep ysstat |awk '{print $3}' |awk -F '.' '{print $1}'").readlines()[0]
            v = int(version.strip())
            if v < 12:
                msg = 'The pidstat version is too low, please upgrade to version 12+, download link: ' \
                      'http://sebastien.godard.pagesperso-orange.fr/download.html'
                logger.error(msg)
                raise Exception(msg)
        except IndexError:
            logger.error(traceback.format_exc())
            msg = 'Please install or upgrade sysstat to version 12+, download link: ' \
                  'http://sebastien.godard.pagesperso-orange.fr/download.html'
            logger.error(msg)
            raise Exception(msg)

    def register_agent(self, disk_flag=True):
        """
        Timed task. One is register, the other one is clean up the ports that stopped monitoring.
        disk_flag: Whether to send email when disk space usage is too high.
        :param
        :return:
        """
        url = f'http://{cfg.getServer("address")}/register'
        header = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json; charset=UTF-8"}
        post_data = {
            'host': self.IP,
            'port': cfg.getAgent('port'),
            'system': self.system_version,
            'cpu': self.cpu_cores,
            'cpu_usage': self.cpu_usage,
            'nic': self.nic,
            'network_speed': self.network_speed,
            'mem': round(self.total_mem, 2),
            'mem_usage': self.mem_usage,
            'disk_size': self.total_disk_h,
            'disk_usage': self.get_used_disk_rate(),
            'disks': ','.join(self.all_disk)
        }
        start_time = time.time()
        disk_start_time = time.time()
        java_start_time = time.time()

        while True:
            try:
                if time.time() - start_time > 8:    # register
                    post_data['cpu_usage'] = self.cpu_usage
                    post_data['mem_usage'] = self.mem_usage
                    res = requests.post(url=url, json=post_data, headers=header)
                    logger.info('Agent registers successful ~')
                    start_time = time.time()

                if time.time() - disk_start_time > 300:
                    disk_usage = self.get_used_disk_rate()
                    if disk_usage:
                        post_data['disk_usage'] = disk_usage    # disk space usage, without %
                        disk_start_time = time.time()

                        if self.maxDiskUsage < disk_usage:
                            msg = f"The disk space usage is {disk_usage/100:.2f}%, it is too high. Server IP is {self.IP}"
                            logger.warning(msg)
                            if self.isDiskAlert and disk_flag:
                                disk_flag = False  # Set to False to prevent cleaning up cache continuously
                                thread = threading.Thread(target=notification, args=(msg,))
                                thread.start()
                        else:
                            disk_flag = True

                if self.java_info['port_status'] == 0 and time.time() - java_start_time > 59:
                    self.get_java_info()
                    java_start_time = time.time()

                time.sleep(5)

            except:
                logger.error(traceback.format_exc())
                time.sleep(3)

    def clear_cache(self):
        """
         Cleaning up cache.
        :return:
        """
        logger.info(f'Start Cleaning up cache: echo {self.echo} > /proc/sys/vm/drop_caches')
        os.popen(f'echo {self.echo} > /proc/sys/vm/drop_caches')
        logger.info('Clear the cache successfully.')

    def __del__(self):
        pass


@handle_exception(is_return=True)
def port_to_pid(port):
    """
     Get pid based on port
    :param port: port
    :return: pid
    """
    pid = None
    result = os.popen(f'netstat -nlp|grep :{port}').readlines()
    logger.debug(f'The result of the port {port} is {result}')
    p = result[0].split()
    pp = p[3].split(':')[-1]
    if str(port) == pp:
        pid = p[p.index('LISTEN') + 1].split('/')[0]
        logger.info(f'The pid of the port {port} is {pid}.')

    return pid


@handle_exception(is_return=True)
def notification(msg):
    """
     Send email.
    :param msg: Email body
    :return:
    """
    url = f'http://{cfg.getServer("address")}/Notification'

    header = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/json; charset=UTF-8"}

    post_data = {
        'host': get_ip(),
        'msg': msg
    }

    logger.debug(f'The content of the email is {msg}')

    res = requests.post(url=url, json=post_data, headers=header)
    if res.status_code == 200:
        response = json.loads(res.content.decode())
        if response['code'] == 0:
            logger.info('Send email successfully.')
        else:
            logger.error(response['msg'])
    else:
        logger.error('Failed to send mail.')
