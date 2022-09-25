# performace_monitor
[中文文档](https://github.com/leeyoshinari/performance_monitor/blob/main/server/static/README_zh_CN.md)

<font color=#FF000><h4>Recommend: [There is a shell tool that integrates server monitor](https://github.com/leeyoshinari/MyPlatform), you can use it to ssh linux and view monitor. Welcome to use it.</h4></font>

## Introduction
#### Completed functions<br>
1. Monitoring the CPU usage, IO wait, Memory, Disk IO, Network, and TCP connections of the server.<br>
2. Monitoring the CPU usage, Context switching, Memory, Disk read and write, and TCP connections of the specified port.<br>
3. For Java applications, monitoring size of JVM and Garbage collection; when the frequency of Full GC is too high, an email alert will be sent.<br>
4. When the Server CPU usage is too high, or free memory is too low, an email alert will be sent; And can clear the cache automatically.<br>
5. Start or Stop monitoring specified port at any time.<br>
6. When the port restarts, it can automatically re-monitor.<br>
7. Support Operational monitoring, when the port stopped, an email alert will be sent.<br>
8. Stopping the Agent directly on the Server.<br>
9. Monitoring data can be visualized according to the specified time period.<br>
10. Calculate the percentiles of the CPU, Disk IO, and Network.<br>
11. Monitoring data sampling frequency is up to about 1 time/sec, and any frequency can be set.<br>
12. The real-time usage of server(CPU, Memory, and Disk) can be viewed directly on the page.<br>
13. A Server can manager multiple Agents at the same time.<br>
14. If Server is stopped, it doesn't affect the monitoring of Agent.

#### Implement
1. Framework: `aiohttp`.<br>
2. Thread pool and queue to monitor.<br>
3. Database: `InfluxDB`.<br>
4. In order to ensure the accuracy of the monitoring data, use Linux's commands to get data directly, and there is no curve fitting during visualization. <br>

## Usage
1. Clone performance_monitor
   ```shell
   git clone https://github.com/leeyoshinari/monitor_agent.git
   ```

2. Deploy InfluxDB, installation steps on CentOS are as follows:<br>
    (1) Download and install<br>
        `wget https://dl.influxdata.com/influxdb/releases/influxdb-1.8.3.x86_64.rpm` <br>
        `yum localinstall influxdb-1.8.3.x86_64.rpm` <br>
    (2) Start<br>
        `systemctl enable influxdb` <br>
        `systemctl start influxdb` <br>
    (3) Modify configuration<br>
         `vim /etc/influxdb/influxdb.conf` <br>
         Around line 256, modify port: `bind-address = ":8086"` <br>
         Around line 265, log disable: `log-enabled = false` <br>
         Restart InfluxDB <br>
    (4) Create database<br>
        `create database test` <br>
        `use test` <br>
        `create user root with password '123456'` create user and password <br>
        `grant all privileges on test to root` grant privileges <br>
   
3. Respectively modify the configuration files `config.ini` in the server and agent folders.<br>

4. Check the version of `sysstat`. Respectively use commands `iostat -V` and `pidstat -V`, the version of `12.4.0` has been tested, if not, please [click me](http://sebastien.godard.pagesperso-orange.fr/download.html) to download it.

5. Respectively run `server.py` in server and agent folders.
   ```shell
   nohup python3 server.py &
   ```
   
## Package
Using `pyinstaller` to package python code. After packaging, it can be quickly deployed agent on other Agents without installing python3.7+ and third-party packages.<br>
Before packaging, you must ensure that the python code can run normally.<br>
    (1) Enter agent folder, run:<br>
    ```shell
    pyinstaller -F server.py -p performance_monitor.py -p logger.py -p config.py -p common.py -p __init__.py --hidden-import logger --hidden-import performance_monitor --hidden-import common --hidden-import config
    ```<br>
    (2) Enter `dist` folder, find the executable file `server`,<br>
    (3) Copy `config.ini` to the `dist` folder,<br>
    (4) Copy the `dist` folder to other servers, and start server
    ```shell
    nohup ./server &
    ```
   NOTE: Since the `agent` needs to run on the server to be monitored, the executable file packaged on the server of the CentOS system X86 architecture can only run on the server of the CentOS system X86 architecture; servers of other system and architecture need to be repackaged `agent`. <br>

## Note
1. The server must support the following commands: `ps`, `jstat`, `iostat`, `pidstat` and `netstat`, if not, please install them. 

2. The network card of server must be in full duplex mode, if not, the network usage will be incorrect.

3. The version of sysstat must be 12+, the 12 version has been tested, other versions have not been tested, and using old version may cause data abnormalities; please [click me](http://sebastien.godard.pagesperso-orange.fr/download.html) to download the latest version.

4. If you don’t know how to install Python3.7+ on Linux server, please [click me](https://github.com/leeyoshinari/performance_monitor/wiki/Python-3.7.x-%E5%AE%89%E8%A3%85).

5. The code can be run on almost any linux system that can run python. The tested systems have `CentOS`, `Ubuntu`, `KylinOS`, `NeoKylin`, support `X86_64` and `ARM` architecture.

6. If you encounter [the issue #8](https://github.com/leeyoshinari/performance_monitor/issues/8) , please rename `draw_performance1.py` to `draw_performance.py`, and rename `performance_monitor1.py` to `performance_monitor.py`. If you know how to solve this issue, please tell me, thank you very much!

## Requirements
1. aiohttp>=3.6.2
2. influxdb>=5.2.3
3. requests
4. Python 3.7+
