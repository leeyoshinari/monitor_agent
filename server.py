#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author: leeyoshinari

import os
import time
import asyncio
import traceback
from aiohttp import web
from common import get_ip
from logger import logger, cfg
from performance_monitor import PerMon, port_to_pid

permon = PerMon()
HOST = get_ip()


async def index(request):
    """
    Home, basic data can be displayed by visiting http://ip:port
    :param request:
    :return:
    """
    return web.Response(
        body=f'The server system version is {permon.system_version}, {permon.cpu_info}, total memory is {permon.total_mem}G, '
             f'the network card is {permon.nic}, bandwidth is {permon.network_speed}Mb/s, {len(permon.all_disk)} disks, '
             f'total size of disks is {permon.total_disk_h}, disks number is {"、".join(permon.all_disk)}. '
             f'If you need to stop the monitoring agent, please visit http://{HOST}:{cfg.getServer("port")}/stop')


async def run_monitor(request):
    """
    Start monitoring port
    :param request:
    :return:
    """
    try:
        data = await request.json()
        is_run = data.get('isRun')
        permon.start = int(is_run)

    except Exception as err:
        logger.error(traceback.format_exc())
        return web.json_response({
            'code': 2, 'msg': str(err), 'data': {'host': HOST, 'port': None, 'pid': None}})


async def get_gc(request):
    """
    Get GC data of java application
    :param request:
    :return:
    """
    port = request.match_info['port']
    try:
        pid = port_to_pid(port)
        if pid is None:
            logger.warning(f"Port {port} not started!")
            return web.json_response({'code': 1, 'msg': f"Port {port} not started!", 'data': None})

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

    except Exception:
        logger.error(traceback.format_exc())
        ygc, ygct, fgc, fgct, fygc, ffgc = -1, -1, -1, -1, '-', -1

    return web.json_response({'code': 0, 'msg': 'Successful!', 'data': [ygc, ygct, fgc, fgct, fygc, ffgc]})


async def stop_monitor(request):
    pid = port_to_pid(cfg.getServer('port'))
    if pid:
        _ = os.popen(f'kill -9 {pid}')
        logger.info('Stop the agent successfully!')
        return web.Response(body='Stop the agent successfully!')
    else:
        return web.Response(body='Agent is not running!')


async def main():
    app = web.Application()

    app.router.add_route('GET', '/', index)
    app.router.add_route('GET', '/stop', stop_monitor)
    app.router.add_route('POST', '/runMonitor', run_monitor)
    app.router.add_route('GET', '/getGC/{port}', get_gc)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, cfg.getServer('port'))
    await site.start()


loop = asyncio.get_event_loop()
loop.run_until_complete(main())
loop.run_forever()
