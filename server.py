#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author: leeyoshinari
import os
import re
import time
import json
import queue
import traceback
from concurrent.futures import ThreadPoolExecutor
import redis
import requests
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import ASYNCHRONOUS
from logger import logger, cfg


class PerMon(object):
    def __init__(self):
        self.thread_pool = 4    # 如果nginx 流量比较大，可适当调整此值，最小值为 4
        self.check_sysstat_version()
        self.IP = get_ip()
        self.room_id = None  # 服务器机房id
        self.group = None   # 服务器所属项目组id
        self.is_monitor = 1  # 是否监控服务器资源
        self.is_nginx = 1   # 是否采集 nginx 流量
        self.monitor_key = ''
        self.nginx_key = ''
        self.redis_host = '127.0.0.1'
        self.redis_port = 6379
        self.redis_password = '123456'
        self.redis_db = 0
        self.influx_url = 'http://127.0.0.1:8086'
        self.influx_org = 'influxdb'
        self.influx_token = 'ZgL0t-L5QmFq-JGQg6XhghW_zVWFfzDoDI05dQ=='
        self.monitor_bucket = 'influxdb'
        self.nginx_bucket = 'influxdb'
        self.period_length = cfg.getMonitor('PeriodLength')
        self.frequencyFGC = cfg.getMonitor('frequencyFGC')
        self.isJvmAlert = cfg.getMonitor('isJvmAlert')
        self.echo = cfg.getMonitor('echo')
        self.system_interval = 1   # If the set value is less than 1, the default is 1

        self.system_version = ''   # system version
        self.cpu_info = ''
        self.cpu_usage = 0.0    # CPU usage
        self.cpu_cores = 0      # number of CPU core
        self.mem_usage = 0.0    # memory usage
        self.io_usage = 0.0
        self.net_usage = 0.0
        self.total_mem = 0      # totel memory, unit: G
        self.total_mem_100 = 0  # total memory, unit: 100*G
        self.nic = ''           # network card
        self.all_disk = []      # disk number
        self.total_disk = 1     # total disk size, unit: M
        self.total_disk_h = 0     # total disk size, unit:T or G
        self.current_disk_rate = self.get_used_disk_rate()  # current disk usage rate
        self.network_speed = cfg.getServer('nicSpeed')  # bandwidth
        self.retrans_num = self.get_retrans_segs()   # TCP retrans number
        self.java_info = {'status': 0, 'pid': '', 'port': '', 'port_status': 0}
        self.gc_info = [-1, -1, -1, -1]     # 'ygc', 'ygct', 'fgc', 'fgct'
        self.ffgc = 999999
        self.prefix = ''

        self.compiler = re.compile(r'(?P<ip>.*?) - \[(?P<time>.*?)\] "(?P<method>.*?) (?P<path>.*?) (?P<protocol>.*?)/.*" (?P<status>.*?) (?P<bytes>.*?) (?P<rt>.*?) "(?P<referer>.*?)" "(?P<jmeter>.*?)"')
        self.access_log = cfg.getNginx('nginxAccessLogPath')

        self.get_config_from_server()
        self.get_system_version()
        self.get_cpu_cores()
        self.get_total_mem()
        self.get_system_nic()
        self.get_disks()
        self.get_system_net_speed()
        self.get_total_disk_size()
        self.get_java_info()
        self.monitor_task = queue.Queue()   # FIFO queue
        self.executor = ThreadPoolExecutor(self.thread_pool)

        self.FGC = {}           # full gc times
        self.FGC_time = {}      # full gc time
        self.last_cpu_usage = [0] * self.period_length   # recently cpu usage
        self.last_net_usage = [0] * self.period_length
        self.last_io_usage = [0] * self.period_length
        self.cpu_flag = True    # Flag of whether to send mail when the CPU usage is too high
        self.mem_flag = True    # Flag of whether to send mail when the free memory is too low
        self.echo_flag = True   # Flag of whether to clean up cache
        self.io_flag = True     # Flag of whether to send mail when the IO is too high
        self.net_flag = True    # Flag of whether to send mail when the Network usage is too high
        if not self.access_log and self.is_nginx:
            self.find_nginx_log()

        self.redis_client = redis.StrictRedis(host=self.redis_host, port=self.redis_port, password=self.redis_password,
                                              db=self.redis_db, decode_responses=True)
        self.client = InfluxDBClient(url=self.influx_url, token=self.influx_token, org=self.influx_org)
        self.write_api = self.client.write_api(write_options=ASYNCHRONOUS)
        self.monitor()

    def get_config_from_server(self):
        url = f'{cfg.getLogging("address")}/register/first'
        post_data = {
            'type': 'monitor-agent',
            'host': self.IP,
            'port': cfg.getServer('port')
        }

        while True:
            try:
                res = http_post(url, post_data)
                logger.info(f"The result of registration is {res}")
                self.redis_host = res['redis']['host']
                self.redis_port = res['redis']['port']
                self.redis_password = res['redis']['password']
                self.redis_db = res['redis']['db']
                self.influx_url = res['influx']['url']
                self.influx_org = res['influx']['org']
                self.influx_token = res['influx']['token']
                self.monitor_bucket = res['influx']['monitor_bucket']
                self.nginx_bucket = res['influx']['nginx_bucket']
                self.group = res['groupKey']
                self.monitor_key = 'server_' + res['groupKey']
                self.nginx_key = 'nginx_' + res['groupKey']
                self.room_id = res['roomId']
                self.prefix = res['prefix']
                self.is_monitor = res['monitor']['is_monitor']
                self.is_nginx = res['monitor']['is_nginx']
                system_interval = res['monitor']['sampling_interval']
                self.system_interval = max(system_interval, 1)  # If the set value is less than 1, the default is 1
                self.system_interval = self.system_interval - 1.05  # Program running time
                self.system_interval = max(self.system_interval, 0.01)
                time.sleep(1)
                break
            except:
                logger.error(traceback.format_exc())
                time.sleep(1)

    def worker(self):
        """
        Get data from the queue and start monitoring
        :return:
        """
        while True:
            func, param = self.monitor_task.get()
            if param:
                func(param)
            else:
                func()
            self.monitor_task.task_done()

    def monitor(self):
        """
        start monitoring
        :return:
        """
        for i in range(self.thread_pool):
            self.executor.submit(self.worker)

        # Put registration and cleanup tasks into the queue
        if self.is_monitor and self.is_nginx: self.monitor_task.put((self.register_agent, None))
        # Put the tasks of the monitoring system into the queue
        if self.is_monitor: self.monitor_task.put((self.write_system_cpu_mem, None))
        # Put the tasks of the monitoring nginx access log into the queue
        if self.is_nginx: self.monitor_task.put((self.parse_log, None))

    def write_system_cpu_mem(self):
        """
        Monitoring system. CPU, Memory, Disk IO, Network, TCP
        :param:
        :return:
        """
        try:
            while True:
                res = self.get_system_cpu_io_speed()
                if res['disk'] is not None and res['cpu'] is not None and res['net'] is not None:
                    pointer = (Point(self.monitor_key)
                               .tag('host', self.IP)
                               .tag('room', self.room_id)
                               .field('cpu', res['cpu'])
                               .field('iowait', res['iowait'])
                               .field('usr_cpu', res['usr_cpu'])
                               .field('mem', res['mem'])
                               .field('mem_available', res['mem_available'])
                               .field('jvm', res['jvm'])
                               .field('disk', res['disk'])
                               .field('disk_r', res['disk_r'])
                               .field('disk_w', res['disk_w'])
                               .field('disk_d', res['disk_d'])
                               .field('rec', res['rec'])
                               .field('trans', res['trans'])
                               .field('net', res['net'])
                               .field('tcp', res['tcp'])
                               .field('retrans', res['retrans'])
                               .field('port_tcp', res['port_tcp'])
                               .field('close_wait', res['close_wait'])
                               .field('time_wait', res['time_wait'])
                               )
                    self.write_api.write(bucket=self.monitor_bucket, org=self.influx_org, record=pointer)

                    self.last_cpu_usage.pop(0)
                    self.last_net_usage.pop(0)
                    self.last_io_usage.pop(0)
                    self.last_cpu_usage.append(res['cpu'])
                    self.last_net_usage.append(res['net'])
                    self.last_io_usage.append(res['disk'])
                    self.cpu_usage = sum(self.last_cpu_usage) / self.period_length  # CPU usage, with %
                    self.mem_usage = 1 - res['mem_available'] / self.total_mem  # Memory usage, without %
                    self.io_usage = sum(self.last_io_usage) / self.period_length
                    self.net_usage = sum(self.last_net_usage) / self.period_length
                    logger.debug(f"system: {res}")
                time.sleep(self.system_interval)
        except:
            logger.error(traceback.format_exc())

    def get_jvm(self, port, pid):
        """
        JVM size
        :param port: port
        :param pid: pid
        :return: jvm(G)
        """
        try:
            result = exec_cmd(f'jstat -gc {pid}')[1]
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
                            self.monitor_task.put((notification, msg))
                    self.gc_info = [int(res[12]), float(res[13]), fgc, float(res[15])]
                    self.ffgc = frequency
                # Write FGC times and time to log
                logger.warning(f"The port {port} has Full GC {self.FGC[str(port)]} times.")
            elif self.FGC[str(port)] > fgc:   # If the times of FGC is reduced, the port may be restarted, then reset it
                self.FGC[str(port)] = 0
            if self.FGC[str(port)] == 0:    # If the times of FGC is 0, reset FGC time.
                self.FGC_time[str(port)] = []
            return mem / 1048576    # 1048576 = 1024 * 1024
        except:
            logger.error(traceback.format_exc())
            self.java_info['status'] = 0
            self.java_info['port_status'] = 0
            return 0.0

    def get_system_cpu_io_speed(self):
        """
        Get system CPU usage, memory, disk IO, network speed, etc.
        :return:
        """
        disk = None
        cpu = None
        iowait = None
        usr_cpu = None
        bps1 = None
        bps2 = None
        rec = None
        trans = None
        network = None
        port_tcp = 0
        close_wait = 0
        time_wait = 0
        jvm = 0.0
        disk1 = []
        disk_r = []
        disk_w = []
        # disk_d = []
        try:
            if self.nic:
                bps1 = exec_cmd(f'cat /proc/net/dev |grep {self.nic}')
                logger.debug(f'The result of speed for the first time is: {bps1}')

            result = exec_cmd('iostat -x -m 1 2')
            logger.debug(f'The result of Disks are: {result}')

            if self.nic:
                bps2 = exec_cmd(f'cat /proc/net/dev |grep {self.nic}')
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
                        disk1.append(float(disk_line[-1]))      # IO
                        disk_r.append(float(disk_line[2]))     # Read MB/s
                        disk_w.append(float(disk_line[8]))     # Write MB/s
                        # disk_d.append(float(disk_line[14]))     # MB/s
                    logger.debug(f'The result of disks are: IO: {disk1}, Read: {disk_r}, Write: {disk_w}')
                    break

            # according to each disk Read and Write calculate IO
            total_disk_r = sum(disk_r)
            total_disk_w = sum(disk_w)
            total_disk = total_disk_r + total_disk_w
            if total_disk == 0:
                disk = 0.0
            else:
                disk_list = [(x + y) / total_disk * z for x, y, z in zip(disk_r, disk_w, disk1)]
                disk = sum(disk_list)

            mem, mem_available = self.get_free_memory()
            if self.java_info['port']:
                tcp_num = self.get_port_tcp(self.java_info['port'])
                port_tcp = tcp_num.get('tcp', 0)
                close_wait = tcp_num.get('close_wait', 0)
                time_wait = tcp_num.get('time_wait', 0)
            if self.java_info['status'] == 1 and self.java_info['port_status'] == 1:
                jvm = self.get_jvm(self.java_info['port'], self.java_info['pid'])  # get JVM size

            if bps1 and bps2:
                data1 = bps1[0].split()
                data2 = bps2[0].split()
                rec = (int(data2[1]) - int(data1[1])) / 1048576
                trans = (int(data2[9]) - int(data1[9])) / 1048576
                # 400 = 8 * 100 / 2
                # Why multiply by 8, because 1MB/s = 8Mb/s.
                # Why divided by 2, because the network card is in full duplex mode.
                network = 400 * (rec + trans) / self.network_speed
                logger.debug(f'The bandwidth of ethernet is: Receive {rec}MB/s, Transmit {trans}MB/s, Ratio {network}%')

            tcp, re_trans = self.get_tcp()
            return {'disk': disk, 'disk_r': total_disk_r, 'disk_w': total_disk_w, 'disk_d': 0.0, 'cpu': cpu, 'iowait': iowait,
                    'usr_cpu': usr_cpu, 'mem': mem, 'mem_available': mem_available, 'rec': rec, 'trans': trans,
                    'net': network, 'tcp': tcp, 'retrans': re_trans, 'port_tcp': port_tcp, 'close_wait': close_wait,
                    'time_wait': time_wait, 'jvm': jvm}
        except:
            logger.error(traceback.format_exc())
            return {}

    @staticmethod
    def get_free_memory():
        """
        Get system memory
        :return: free Memory, available Memory
        """
        mem, mem_available = 0, 0
        result = exec_cmd('cat /proc/meminfo')
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

    def get_tcp(self):
        """
        Get the number of TCP and calculate the retransmission rate
        :return:
        """
        result = exec_cmd('cat /proc/net/snmp |grep Tcp')
        tcps = result[-1].split()
        logger.debug(f'The TCP is: {tcps}')
        tcp = int(tcps[9])      # TCP connections
        re_trans = int(tcps[-4]) - self.retrans_num
        self.retrans_num = int(tcps[-4])
        return tcp, re_trans

    def get_port_tcp(self, port):
        """
        Get the number of TCP connections for the port
        :param port: port
        :return:
        """
        tcp_num = {}
        try:
            with os.popen(f'ss -ant |grep {port}') as p:
                res = p.read()
            if res.count('LISTEN') == 0:
                self.java_info['port_status'] = 0
                self.java_info['status'] = 0
                self.gc_info = [-1, -1, -1, -1]
                return tcp_num
            tcp_num.update({'tcp': res.count(f':{port}')})
            tcp_num.update({'established': res.count('ESTAB')})
            tcp_num.update({'close_wait': res.count('CLOSE-WAIT')})
            tcp_num.update({'time_wait': res.count('TIME-WAIT')})
        except:
            logger.error(traceback.format_exc())
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
            result = exec_cmd('cat /proc/cpuinfo | grep "model name" |uniq')[0]
            cpu_model = result.strip().split(':')[1].strip()
            logger.info(f'The CPU model is {cpu_model}')
        except Exception as err:
            logger.error('The CPU model is not found.')
            logger.error(err)

        try:
            result = exec_cmd('cat /proc/cpuinfo | grep "physical id" | uniq | wc -l')[0]
            cpu_num = int(result)
            logger.info(f'The number of CPU is {cpu_num}')
        except Exception as err:
            logger.error('The number of CPU is not found.')
            logger.error(err)

        try:
            result = exec_cmd('cat /proc/cpuinfo | grep "cpu cores" | uniq')[0]
            cpu_core = int(result.strip().split(':')[1].strip())
            logger.info(f'The number of cores per CPU is {cpu_core}')
        except Exception as err:
            logger.error('The number of cores per CPU is not found.')
            logger.error(err)

        result = exec_cmd('cat /proc/cpuinfo| grep "processor"| wc -l')[0]
        self.cpu_cores = int(result)
        logger.info(f'The number of cores all CPU is {self.cpu_cores}')

        if cpu_model and cpu_num and cpu_core:
            self.cpu_info = f'{cpu_num} CPU(s), {cpu_core} core(s) pre CPU, total {self.cpu_cores} cores, ' \
                            f'CPU model is {cpu_model} '
        elif cpu_model:
            self.cpu_info = f'total CPU cores is {self.cpu_cores}, CPU model is {cpu_model} '
        else:
            self.cpu_info = f'total CPU cores is {self.cpu_cores}'

    def get_total_mem(self):
        """
        Get Memory
        :return:
        """
        result = exec_cmd('cat /proc/meminfo| grep "MemTotal"')[0]
        self.total_mem = float(result.split(':')[-1].split('k')[0].strip()) / 1048576   # 1048576 = 1024 * 1024
        self.total_mem_100 = self.total_mem / 100
        logger.info(f'The total memory is {self.total_mem}G')

    def get_disks(self):
        """
        Get all disks number.
        :return:
        """
        result = exec_cmd('iostat -x -k')
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

    def get_system_nic(self):
        """
        Get network card.
        Only one network card can be got. If the system uses multiple network cards, only the first one can
        be got. Use "cat /proc/net/dev" to view the order of the network cards.
        :return:
        """
        try:
            network_card = []
            result = exec_cmd('cat /proc/net/dev')   # get network data
            logger.debug(f'The result for the first time is: {result}')
            time.sleep(1)
            result1 = exec_cmd('cat /proc/net/dev')  # get network data again
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
        except:
            logger.error(traceback.format_exc())

    def get_total_disk_size(self):
        """
        Get disk size
        :return:
        """
        try:
            result = exec_cmd('df -m')
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
        except:
            logger.error(traceback.format_exc())

    def get_used_disk_rate(self):
        """
        Get disks usage
        :return:
        """
        used_disk_size = 0
        try:
            result = exec_cmd('df -m')
            logger.debug(f'The data of disk is {result}')
            for line in result:
                res = line.split()
                if '/dev/' in res[0]:
                    used_disk_size += float(res[2])
            logger.info(f'The used size of disks is {used_disk_size}M')
        except:
            logger.error(traceback.format_exc())
        return used_disk_size / self.total_disk

    def get_system_net_speed(self):
        """
        Get bandwidth, Mbs
        :return:
        """
        try:
            if self.nic:
                result = exec_cmd(f'ethtool {self.nic}')
                logger.debug(f'The bandwidth is {result}')
                for line in result:
                    if 'Speed' in line:
                        logger.debug(f'The bandwidth is {line}')
                        res = re.findall(r"(\d+)", line)
                        if res:
                            try:
                                speed = int(res[0])
                                if 'G' in line:
                                    speed = speed * 1024
                                if 'K' in line:
                                    speed = speed / 1024
                                self.network_speed = speed
                                break
                            except IndexError:
                                logger.error(traceback.format_exc())
                logger.info(f'The bandwidth of ethernet is {self.network_speed}Mb/s')
        except:
            logger.error(traceback.format_exc())

    def get_system_version(self):
        """
        Get system version
        :return:
        """
        try:
            result = exec_cmd('cat /etc/redhat-release')[0]    # system release version
            logger.debug(f'The system release version is {result}')
            self.system_version = result.strip()
        except Exception as err:
            logger.warning(err)
            result = exec_cmd('cat /proc/version')[0]   # system kernel version
            logger.debug(f'The system kernel version is {result}')
            res = re.findall(r"gcc.*\((.*?)\).*GCC", result.strip())
            if res:
                self.system_version = res[0]
            else:
                res = re.findall(r"gcc.*\((.*?)\)", result.strip())
                self.system_version = res[0]
        logger.info(f'system release/kernel version is {self.system_version}')

    def get_retrans_segs(self):
        """
        Get the number of TCP RetransSegs
        :return:
        """
        try:
            result = exec_cmd('cat /proc/net/snmp |grep Tcp')
            tcps = result[-1].split()
            logger.debug(f'The TCP is: {tcps}')
            return int(tcps[-4])
        except:
            logger.error(traceback.format_exc())
            return 0

    def get_java_info(self):
        """
        Find java service
        """
        try:
            if self.java_info['status'] == 0 and self.java_info['port_status'] == 0:
                pid = exec_cmd("ps -ef |grep " + self.group + " |grep -v grep |tr -s ' ' |awk '{print $2}'")
                if pid and pid[0].strip():
                    self.java_info['pid'] = pid[0].strip()
                    port = exec_cmd("ss -antpl|grep " + self.java_info['pid'] + " |tr -s ' ' |awk '{print $4}' | awk -F ':' '{print $2}'")
                    if port and port[0].strip():
                        self.java_info['port'] = port[0].strip()
                    try:
                        result = exec_cmd(f'jstat -gc {self.java_info["pid"]} |tr -s " "')[1]
                        res = result.strip().split(' ')
                        logger.info(f'The JVM of {pid} is {res}')
                        _ = float(res[2]) + float(res[3]) + float(res[5]) + float(res[7])
                        self.java_info['status'] = 1
                        self.java_info['port_status'] = 1
                    except Exception as err:
                        logger.warning(err)
                        self.java_info['status'] = 0
                        self.java_info['port_status'] = 0
                else:
                    logger.warning(f"Not found java service, group name is {self.group}")
                    self.java_info['status'] = 0
                    self.java_info['port_status'] = 0
        except:
            logger.error(traceback.format_exc())
            self.java_info['status'] = 0
            self.java_info['port_status'] = 0

    @staticmethod
    def check_sysstat_version():
        """
        Check sysstat version
        """
        try:
            version = exec_cmd("iostat -V |grep ysstat |awk '{print $3}' |awk -F '.' '{print $1}'")[0]
            v = int(version.strip())
            if v < 12:
                msg = 'The iostat version is too low, please upgrade to version 12+, download link: ' \
                      'https://sysstat.github.io/versions.html'
                logger.error(msg)
                raise Exception(msg)
        except IndexError:
            logger.error(traceback.format_exc())
            msg = 'Please install or upgrade sysstat to version 12+, download link: ' \
                  'https://sysstat.github.io/versions.html'
            logger.error(msg)
            raise Exception(msg)

    def register_agent(self):
        """
        Timed task. One is register, the other one is clean up the ports that stopped monitoring.
        disk_flag: Whether to send email when disk space usage is too high.
        :param
        :return:
        """
        post_data = {
            'host': self.IP,
            'port': cfg.getServer('port'),
            'system': self.system_version,
            'cpu': self.cpu_cores,
            'cpu_usage': self.cpu_usage,
            'nic': self.nic,
            'net_usage': self.net_usage,
            'network_speed': self.network_speed,
            'mem': round(self.total_mem, 2),
            'mem_usage': self.mem_usage,
            'io_usage': self.io_usage,
            'disk_size': self.total_disk_h,
            'disk_usage': self.current_disk_rate,
            'disks': ','.join(self.all_disk),
            'gc': self.gc_info,
            'ffgc': self.ffgc
        }
        start_time = time.time()
        disk_start_time = time.time()
        java_start_time = time.time()
        while True:
            try:
                if time.time() - start_time > 8:  # register
                    post_data['cpu_usage'] = self.cpu_usage
                    post_data['mem_usage'] = self.mem_usage
                    post_data['io_usage'] = self.io_usage
                    post_data['net_usage'] = self.net_usage
                    post_data['gc'] = self.gc_info
                    post_data['ffgc'] = self.ffgc
                    self.redis_client.set(name='Server_' + self.IP, value=json.dumps(post_data, ensure_ascii=False), ex=12)
                    logger.info('Agent registers successful ~')
                    start_time = time.time()
                if time.time() - disk_start_time > 300:
                    disk_usage = self.get_used_disk_rate()
                    if disk_usage:
                        post_data['disk_usage'] = disk_usage  # disk space usage, without %
                        disk_start_time = time.time()
                if self.java_info['port_status'] == 0 and time.time() - java_start_time > 59:
                    self.get_java_info()
                    post_data['gc'] = self.gc_info
                    post_data['ffgc'] = self.ffgc
                    java_start_time = time.time()
                time.sleep(2)
            except:
                logger.error(traceback.format_exc())
                time.sleep(3)

    def find_nginx_log(self):
        try:
            with os.popen("ps -ef|grep nginx |grep -v grep |grep master|awk '{print $2}'") as p:
                nginx_pid = p.read().strip()
            logger.info(f'nginx pid is: {nginx_pid}')
            if nginx_pid:
                with os.popen(f'pwdx {nginx_pid}') as p:
                    res = p.read()
                nginx_path = res.strip().split(' ')[-1].strip()
                self.access_log = os.path.join(os.path.dirname(nginx_path), 'logs', 'access.log')
                if not os.path.exists(self.access_log):
                    logger.error(f'Not found nginx log: {self.access_log}')
            else:
                logger.error('Nginx is not found ~')
        except:
            logger.error(traceback.format_exc())

    def parse_log(self):
        logger.info(f'Nginx log path: {self.access_log}')
        position = 0
        with open(self.access_log, mode='r', encoding='utf-8') as f1:
            lines = f1.readlines()   # jump to current newest line, ignore old lines.
            while True:
                try:
                    lines = f1.readlines()
                    cur_position = f1.tell()
                    if cur_position == position:
                        time.sleep(0.01)
                        continue
                    else:
                        position = cur_position
                        for line in lines:
                            self.monitor_task.put((self.parse_line, line))
                except:
                    logger.error(traceback.format_exc())

    def parse_line(self, line):
        if self.prefix in line and '/static/' not in line:
            logger.debug(f'Nginx - access.log -- {line}')
            res = self.compiler.match(line).groups()
            logger.debug(res)
            path = res[3].split('?')[0].strip()
            if 'JMETER' in res[9]:
                source = 'Jmeter'
            else:
                source = 'Normal'
            c_time = res[1].split('+')[0].replace('T', ' ').strip()
            try:
                rt = float(res[7].split(',')[-1].strip()) if ',' in res[7] else float(res[7].strip())
            except ValueError:
                logger.error(f'parse error: {line}')
                rt = 0
            error = 0 if int(res[5]) < 400 else 1
            pointer = (Point(self.nginx_key)
                       .tag('source', source)
                       .tag('path', path)
                       .field('c_time', c_time)
                       .field('client', res[0].strip())
                       .field('status', int(res[5]))
                       .field('size', int(res[6]))
                       .field('rt', int(rt * 1000))
                       .field('error', error)
                       )
            self.write_api.write(bucket=self.nginx_bucket, org=self.influx_org, record=pointer)

    def clear_cache(self):
        """
         Cleaning up cache.
        :return:
        """
        logger.info(f'Start Cleaning up cache: echo {self.echo} > /proc/sys/vm/drop_caches')
        _ = exec_cmd(f'echo {self.echo} > /proc/sys/vm/drop_caches')
        logger.info('Clear the cache successfully.')


def exec_cmd(cmd):
    try:
        with os.popen(cmd) as p:
            res = p.readlines()
        return res
    except:
        raise


def port_to_pid(port):
    """
     Get pid based on port
    :param port: port
    :return: pid
    """
    pid = None
    try:
        result = exec_cmd(f'ss -nlp|grep :{port}')
        logger.debug(f'The result of the port {port} is {result}')
        p = result[0].strip().split()
        pp = p[4].split(':')[-1]
        if str(port) == pp:
            pid = p[-1].split('pid=')[-1].split(',')[0]
            logger.info(f'The pid of the port {port} is {pid}.')
    except:
        logger.error(traceback.format_exc())
    return pid


def notification(msg):
    """
     Send email.
    :param msg: Email body
    :return:
    """
    try:
        url = f'http://{cfg.getLogging("address")}/setMessage'
        header = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json; charset=UTF-8"}
        post_data = {'host': get_ip(), 'msg': msg}
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
    except:
        logger.error(traceback.format_exc())


def get_ip():
    """
    Get server's IP address
    :return: IP address
    """
    ip = '127.0.0.1'
    try:
        if cfg.getServer('host'):
            ip = cfg.getServer('host')
        else:
            result = exec_cmd("hostname -I |awk '{print $1}'")
            logger.debug(result)
            if result:
                ip = result[0].strip()
                logger.info(f'The IP address is: {ip}')
            else:
                logger.warning('Server IP address not found!')
    except:
        logger.error(traceback.format_exc())
    return ip


def http_post(url, post_data):
    header = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/json; charset=UTF-8"}
    try:
        res = requests.post(url=url, json=post_data, headers=header)
        if res.status_code == 200:
            response_data = json.loads(res.content.decode('unicode_escape'))
            if response_data['code'] == 0:
                return response_data['data']
            else:
                logger.error(response_data['msg'])
                raise Exception(response_data['msg'])
    except:
        logger.error(traceback.format_exc())
        raise


if __name__ == '__main__':
    perf = PerMon()
    time.sleep(2)
    PID = os.getpid()
    with open('pid', 'w', encoding='utf-8') as f:
        f.write(str(PID))
