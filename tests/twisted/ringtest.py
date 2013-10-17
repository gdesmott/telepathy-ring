
"""
Infrastructure code for testing Ring
"""

import os
import sys
import dbus
import servicetest
import time
import ConfigParser
import subprocess

from servicetest import (unwrap, Event)
from twisted.internet import reactor
from twisted.internet.protocol import Factory, Protocol
from twisted.internet.endpoints import TCP4ClientEndpoint

def install_colourer():
    def red(s):
        return '\x1b[31m%s\x1b[0m' % s

    def green(s):
        return '\x1b[32m%s\x1b[0m' % s

    patterns = {
        'handled': green,
        'not handled': red,
        }

    class Colourer:
        def __init__(self, fh, patterns):
            self.fh = fh
            self.patterns = patterns

        def write(self, s):
            f = self.patterns.get(s, lambda x: x)
            self.fh.write(f(s))

    sys.stdout = Colourer(sys.stdout, patterns)
    return sys.stdout

class PhoneSimProtocol(Protocol):
    pass

class PhoneSimProtocolFactory(Factory):
    def buildProtocol(self, addr):
        return PhoneSimProtocol()

class PhoneSim(object):
    executable = 'phonesim'
    xml = '/usr/share/phonesim/default.xml'

    def __init__(self, name, port, q):
        self.name = name
        self.port = port
        self.q = q
        self.path = '/' + name
        self.process = None
        self.protocol = None

    def start(self):
        self.process = subprocess.Popen([PhoneSim.executable, '-p',
                self.port, PhoneSim.xml])

        self.try_connecting()

    def try_connecting(self):
        point = TCP4ClientEndpoint(reactor, '127.0.0.1', int(self.port))
        d = point.connect(PhoneSimProtocolFactory())
        d.addCallback(self.connected)
        # We don't have any way to know when Phonesim is up and running, so
        # retry to connect every 0.1 seconds until it works.
        reactor.callLater(0.1, self.retry, d)

    def retry(self, d):
        if self.protocol is None:
            d.cancel()
            self.try_connecting()

    def connected(self, protocol):
        if protocol is None:
            return

        self.protocol = protocol

        self.q.append(Event('phonesim-connected',
            phonesim=self,
            protocol=protocol))

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process = None

    def get_interface(self, bus, interface):
        return dbus.Interface(bus.get_object('org.ofono', self.path),
            dbus_interface=interface)

class Simulator(object):
    ofono_phonesim_conf = '/etc/ofono/phonesim.conf'

    def __init__(self, q, bus):
        self.q = q
        self.bus = bus
        self.phonesims = []

        try:
            manager = dbus.Interface(self.bus.get_object('org.ofono', '/'),
                dbus_interface='org.ofono.Manager')
        except dbus.exceptions.DBusException:
              print "  Ofono needs to be running to execute tests"
              os._exit(1)

        modems = manager.GetModems()
        parser = ConfigParser.RawConfigParser()
        parser.read(Simulator.ofono_phonesim_conf)
        for i in modems:
            path, properties = i
            section = path[1:]
            if parser.has_section(section) and \
                            parser.get(section, 'Address') == '127.0.0.1':
                    self.phonesims.append(PhoneSim(section,
                            parser.get(section, 'Port'), self.q))

        if len(self.phonesims) == 0:
            print "  You have to configure at least one phonesim modem in %s" % Simulator.ofono_phonesim_conf
            self.cleanup()
            os._exit(1)

    def cleanup(self):
        for i in self.phonesims:
            i.stop()

    def available_simulators(self):
        return len(self.phonesims)

    def set_simulator_online(self, index):
        phonesim = self.phonesims[index]
        modem = phonesim.get_interface(self.bus, 'org.ofono.Modem')

        # Catch D-Bus signals from this modem and expose them as modem-signal
        # events. We can't use the usual dbus-signal events as those are on
        # the session bus while ofono events are on the system bus.
        self.bus.add_signal_receiver(
            lambda *args, **kw:
                self.q.append(
                    Event('modem-signal',
                        path=unwrap(kw['path']),
                        phonesim=phonesim,
                        signal=kw['member'], args=map(unwrap, args),
                        interface=kw['interface'])),
            path=phonesim.path,
            path_keyword='path',
            member_keyword='member',
            interface_keyword='interface',
            byte_arrays=True
            )

        phonesim.start()

        self.q.expect('phonesim-connected')

        # Power on the modem
        modem.SetProperty('Powered', dbus.Boolean(1))
        self.q.expect('modem-signal', signal='PropertyChanged', phonesim=phonesim,
            args=['Powered', True])

        # Put it online
        modem.SetProperty('Online', dbus.Boolean(1))
        self.q.expect('modem-signal', signal='PropertyChanged', phonesim=phonesim,
            args=['Online', True])

        # Wait for the interfaces we care about to be ready
        # FIXME: this is not good enough as we may receive this signal before
        # ring...
        self.q.expect('modem-signal', signal='PropertyChanged', phonesim=phonesim,
            predicate=lambda e: e.args[0] == 'Interfaces' and
            'org.ofono.MessageManager' in e.args[1])

        return phonesim

def make_connection(bus, event_func, params, phonesim):
    default_params = {
        'modem': dbus.ObjectPath(phonesim.path),
        }

    if params:
        default_params.update(params)

    return servicetest.make_connection(bus, event_func, 'ring', 'tel',
        default_params)

def exec_test_deferred (funs, params, protocol=None, timeout=None):
    colourer = None

    if sys.stdout.isatty():
        colourer = install_colourer()

    queue = servicetest.IteratingEventQueue(timeout)
    queue.verbose = (
        os.environ.get('CHECK_TWISTED_VERBOSE', '') != ''
        or '-v' in sys.argv)

    bus = dbus.SessionBus()

    bus.add_signal_receiver(
        lambda *args, **kw:
            queue.append(
                Event('dbus-signal',
                    path=unwrap(kw['path']),
                    signal=kw['member'], args=map(unwrap, args),
                    interface=kw['interface'])),
        None,       # signal name
        None,       # interface
        None,
        path_keyword='path',
        member_keyword='member',
        interface_keyword='interface',
        byte_arrays=True
        )

    sim = Simulator(queue, dbus.SystemBus())
    phonesim = sim.set_simulator_online(0)

    try:
        for f in funs:
            conn = make_connection(bus, queue.append, params, phonesim)
            f(queue, bus, conn, phonesim)
    except Exception:
        import traceback
        traceback.print_exc()

    sim.cleanup()

    try:
        if colourer:
          sys.stdout = colourer.fh
        reactor.crash()

        # force Disconnect in case the test crashed and didn't disconnect
        # properly.  We need to call this async because the BaseIRCServer
        # class must do something in response to the Disconnect call and if we
        # call it synchronously, we're blocking ourself from responding to the
        # quit method.
        servicetest.call_async(queue, conn, 'Disconnect')

        if 'RING_TEST_REFDBG' in os.environ:
            # we have to wait for the timeout so the process is properly
            # exited and refdbg can generate its report
            time.sleep(5.5)

    except dbus.DBusException:
        pass

def exec_tests(funs, params=None, protocol=None, timeout=None):
  reactor.callWhenRunning (exec_test_deferred, funs, params, protocol, timeout)
  reactor.run()

def exec_test(fun, params=None, protocol=None, timeout=None):
  exec_tests([fun], params, protocol, timeout)

