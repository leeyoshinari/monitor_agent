# monitor_agent
[English](https://github.com/leeyoshinari/monitor_agent/blob/main/README.md)

如需了解更多，可查看[源项目](https://github.com/leeyoshinari/performance_monitor) 。本项目是在[这个源项目](https://github.com/leeyoshinari/performance_monitor) 的基本上修改而来的，仅适用于和[这个项目](https://github.com/leeyoshinari/MyPlatform) 搭配使用。

## 介绍
#### 已完成如下功能<br>
1、监控整个服务器的CPU使用率、io wait、内存使用、磁盘IO、网络带宽和TCP连接数<br>
2、针对java应用，可以监控jvm大小和垃圾回收情况；当Full GC频率过高时，可发送邮件提醒<br>
3、系统CPU使用率过高，或者剩余内存过低时，可发送邮件提醒；可设置自动清理缓存<br>
4、数据采样频率最高可达约1次/s，可设置任意采样频率<br>

#### 实现过程
1、使用基于协程的http框架`aiohttp`<br>
2、使用influxDB数据库存储监控数据；数据可设置自动过期时间<br>
3、为保证监控结果准确性，直接使用Linux系统命令获取数据，且可视化时未做任何曲线拟合处理<br>
<br>

## 使用
1. 克隆 performance_monitor
   ```shell
   git clone https://github.com/leeyoshinari/monitor_agent.git
   ```
   
2. 部署InfluxDB数据库。CentOS安装过程如下：<br>
    （1）下载并安装<br>
        `wget https://dl.influxdata.com/influxdb/releases/influxdb-1.8.3.x86_64.rpm` <br>
        `yum localinstall influxdb-1.8.3.x86_64.rpm` <br>
    （2）启动<br>
        `systemctl enable influxdb` <br>
        `systemctl start influxdb` <br>
    （3）修改配置<br>
         `vim /etc/influxdb/influxdb.conf` <br>
         第256行左右，修改端口：`bind-address = ":8086"` <br>
         第265行左右，不打印日志：`log-enabled = false` <br>
         重启 <br>
    （4）创建数据库<br>
        `create database test` <br>
        `use test` <br>
        `create user root with password '123456'` 创建用户和设置密码 <br>
        `grant all privileges on test to root` 授权数据库给指定用户 <br>

3. 修改配置文件 `config.ini`

4. 检查`sysstat`版本。分别使用`iostat -V`命令，`12.4.0`版本已经测试过了，如果不是这个版本，[请点我](http://sebastien.godard.pagesperso-orange.fr/download.html) 下载。

5. 运行`server.py`
   ```shell
   nohup python3 server.py &
   ```
   
## 打包
pyinstaller既可以将python脚本打包成Windows环境下的可执行文件，也可以打包成Linux环境下的可执行文件。打包完成后，可快速在其他环境上部署该监控服务，而不需要安装python3.7+环境和第三方包。<br>

pyinstaller安装过程自行百度，下面直接进行打包：<br>

    (1)安装好python环境，安装第三方包，确保程序可以正常运行；<br>
    (2)进入文件夹，开始打包：<br>
    ```shell
    pyinstaller -F server.py -p logger.py -p config.py -p __init__.py --hidden-import logger --hidden-import config
    ```
    (3)打包完成后，在当前路径下会生成dist文件夹，进入`dist`即可找到可执行文件`server`;<br>
    (4)将配置文件`config.conf`拷贝到`dist`文件夹下，并修改配置文件；<br>
    (5)压缩文件`zip monitor_agent.zip server config.conf`；<br>
    (6)上传压缩包到 [MyPlatform](https://github.com/leeyoshinari/MyPlatform.git) ，并部署；<br>
   `由于需要在待监控的服务器上运行，在CentOS系统X86架构的服务器上打包完成的可执行文件，只能运行在CentOS系统X86架构的服务器上；其他系统和架构的服务器需要重新打包。`<br>

## 注意
1. 服务器必须支持以下命令：`jstat`、`iostat`，如不支持，请安装。

2. sysstat的版本必须是12+，目前测试过12的版本，其他版本未测试过，使用老版本可能会导致数据异常；最新版本下载地址[请点我](http://sebastien.godard.pagesperso-orange.fr/download.html) 。

3. 如果你不知道怎么在Linux服务器上安装好Python3.7+，[请点我](https://github.com/leeyoshinari/performance_monitor/wiki/Python-3.7.x-%E5%AE%89%E8%A3%85) 。

4. 服务器网卡必须是全双工模式，如果不是，带宽使用率计算将会不正确。

5. 当前程序几乎可以运行在任何可以运行python的linux系统上，已测试过的系统`CentOS`、`Ubuntu`、`中标麒麟`、`银河麒麟`，支持`X86_64`和`ARM`架构。

## Requirements
1. redis>=4.1.0
2. requests>=2.24.0
3. Python 3.7+
