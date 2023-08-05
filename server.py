#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author: leeyoshinari

import os
import time
import json
import traceback
from flask import Flask, Response
from logger import logger, cfg
from performance_monitor import PerMon, port_to_pid


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
            result = os.popen("hostname -I |awk '{print $1}'").readlines()
            if result:
                ip = result[0].strip()
                logger.info(f'The IP address is: {ip}')
            else:
                logger.warning('Server IP address not found!')
    except:
        logger.error(traceback.format_exc())
    return ip


app = Flask(__name__)
permon = PerMon()
HOST = get_ip()
PID = os.getpid()
with open('pid', 'w', encoding='utf-8') as f:
    f.write(str(PID))


@app.route('/', methods=['GET'])
def index():
    """
    Home, basic data can be displayed by visiting http://ip:port
    :param request:
    :return:
    """
    return Response(status=200,
        response=f'The server system version is {permon.system_version}, {permon.cpu_info}, total memory is {permon.total_mem}G, '
             f'the network card is {permon.nic}, bandwidth is {permon.network_speed}Mb/s, {len(permon.all_disk)} disks, '
             f'total size of disks is {permon.total_disk_h}, disks number is {"、".join(permon.all_disk)}. '
             f'If you need to stop the monitoring agent, please visit http://{HOST}:{cfg.getServer("port")}/stop')


# @app.route('/runMonitor/<isRun>', methods=['GET'])
# def run_monitor(isRun):
#     """
#     Start monitoring port
#     :param request:
#     :return:
#     """
#     try:
#         permon.start = int(isRun)
#     except Exception as err:
#         logger.error(traceback.format_exc())
#         return web.json_response({'code': 2, 'msg': str(err), 'data': {'host': HOST, 'port': None, 'pid': None}})


@app.route('/getGC/<port>', methods=['GET'])
async def get_gc(port):
    """
    Get GC data of java application
    :param port:
    :return:
    """
    try:
        pid = port_to_pid(port)
        if pid is None:
            logger.warning(f"Port {port} not started!")
            return Response(response=json.dumps({'code': 1, 'msg': f"Port {port} not started!", 'data': None}),
                            status=200, mimetype='application/json')
        result = os.popen(f'jstat -gc {pid} |tr -s " "').readlines()[1]
        res = result.strip().split(' ')

        # Current GC data
        ygc = int(res[12])
        ygct = float(res[13])
        fgc = int(res[14])
        fgct = float(res[15])
        fygc = '-'
        ffgc = 0

        # Historical GC data
        fgc_history = permon.FGC[port]
        fgc_time_history = permon.FGC_time[port]
        if fgc > 0:
            if fgc == fgc_history:
                if len(fgc_time_history) > 1:
                    ffgc = round(time.time() - fgc_time_history[-2], 2)
                else:
                    result = os.popen(f'ps -p {pid} -o etimes').readlines()[1]  # the running time of the process
                    runtime = int(result.strip())
                    ffgc = round(runtime / fgc, 2)
            else:
                ffgc = round(time.time() - fgc_time_history[-1], 2)
        else:
            fgc = -1
        del res, fgc_time_history
    except:
        logger.error(traceback.format_exc())
        ygc, ygct, fgc, fgct, fygc, ffgc = -1, -1, -1, -1, '-', -1

    return Response(response=json.dumps({'code': 0, 'msg': 'Successful!', 'data': [ygc, ygct, fgc, fgct, fygc, ffgc]}),
                    status=200, mimetype='application/json')


if __name__ == '__main__':
    app.run(host=HOST, port=cfg.getServer('port'), debug=False)
