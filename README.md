# monitor_agent
It can be only used with [MyPlatform](https://github.com/leeyoshinari/MyPlatform.git), and can't be used alone. <br>

This Repository is modified based on the [Source Repository](https://github.com/leeyoshinari/performance_monitor). If you want to know more information, [please access it](https://github.com/leeyoshinari/performance_monitor).

## Deploy
1. Clone Repository
   ```shell
   git clone https://github.com/leeyoshinari/monitor_agent.git
   ```

2. Modify the configuration files `config.conf`.<br>

3. Modify Nginx log format in `nginx.conf`. <br>
    Custom log format is
    ```
    log_format  main   '$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent $upstream_response_time "$http_referer" "$http_performance_header" "$http_user_agent"';
   ```
   Use custom log format 
   ```
   access_log  logs/access.log  main;
   ``` 
   
   If you want to output other information, please add it to end of log format.

4. Package. Using `pyinstaller` to package python code. 
- (1) Enter folder, run:<br>
    ```shell
    pyinstaller -F server.py -p logger.py -p config.py -p __init__.py --hidden-import logger --hidden-import config
    ```
- (2) Copy `config.conf` to the `dist` folder, cmd: `cp config.conf dist/`
- (3) Enter `dist` folder, zip files, cmd: `zip monitor_agent.zip server config.conf`
- (4) Upload zip file to [MyPlatform](https://github.com/leeyoshinari/MyPlatform.git)
- (5) Deploy monitor_agent
   
NOTE: For Linux Server, the executable file packaged on the server of the CentOS system X86 architecture can only run on the server of the CentOS system X86 architecture; servers of other system and architecture need to be repackaged. <br>

## Note
1. The server must support the following commands: `ps`, `jstat` and `iostat`, if not, please install them. 

2. The network card of server must be in full duplex mode, if not, the network usage will be incorrect.

3. The version of sysstat must be 12+, the version of `12.4.0` has been tested, other versions have not been tested, and using old version may cause data abnormalities; please [click me](http://sebastien.godard.pagesperso-orange.fr/download.html) to download it.
