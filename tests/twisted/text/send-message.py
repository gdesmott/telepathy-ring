
"""
Test connecting to a server.
"""

from ringtest import exec_test
from servicetest import EventPattern, call_async, assertEquals, assertContains, wrap_channel
import constants as cs

CONTACT_ID = '+321234567'

def test(q, bus, conn, phonesim):
    conn.Connect()
    q.expect('dbus-signal', signal='StatusChanged', args=[cs.CONN_STATUS_CONNECTED, cs.CSR_REQUESTED])

    self_handle = conn.Properties.Get(cs.CONN, 'SelfHandle')

    call_async(q, conn.Requests, 'CreateChannel', {
        cs.CHANNEL_TYPE: cs.CHANNEL_TYPE_TEXT,
        cs.TARGET_HANDLE_TYPE: cs.HT_CONTACT,
        cs.TARGET_ID: CONTACT_ID,
        })

    e = q.expect('dbus-signal', signal='NewChannels')
    path, props = e.args[0][0]

    # check channel properties
    assertEquals(cs.CHANNEL_TYPE_TEXT, props[cs.CHANNEL_TYPE])
    assertEquals(cs.HT_CONTACT, props[cs.TARGET_HANDLE_TYPE])
    assertEquals(CONTACT_ID, props[cs.TARGET_ID])
    assertEquals(self_handle, props[cs.INITIATOR_HANDLE])
    assertEquals('<SelfHandle>', props[cs.INITIATOR_ID])
    assertEquals(True, props[cs.REQUESTED])
    assertContains(cs.CHANNEL_IFACE_DESTROYABLE, props[cs.INTERFACES])
    assertContains(cs.CHANNEL_IFACE_MESSAGES, props[cs.INTERFACES])
    assertContains(cs.CHANNEL_IFACE_SMS, props[cs.INTERFACES])

    assertEquals(cs.DELIVERY_REPORTING_SUPPORT_FLAGS_RECEIVE_FAILURES |
            cs.DELIVERY_REPORTING_SUPPORT_FLAGS_RECEIVE_SUCCESSES, props[cs.DELIVERY_REPORTING_SUPPORT])
    assertEquals(0, props[cs.MESSAGE_PART_SUPPORT_FLAGS])
    assertContains('text/plain', props[cs.SUPPORTED_CONTENT_TYPES])
    assertContains('text/x-vcard', props[cs.SUPPORTED_CONTENT_TYPES])

    assertEquals(False, props[cs.SMS_FLASH])
    assertEquals(True, props[cs.SMS_CHANNEL])

    chan = wrap_channel(bus.get_object(conn.bus_name, path), 'Text')

    msg = [ { 'message-type': cs.MT_NORMAL },
            { 'content-type': 'text/plain',
              'content': 'Oh hi' } ]

    call_async(q, chan.Messages, 'SendMessage', msg, 0)

    e = q.expect('dbus-signal', signal='MessageSent', path=path)

    sent_msg, flags, token = e.args
    assertEquals(self_handle, sent_msg[0]['message-sender'])
    assertEquals('<SelfHandle>', sent_msg[0]['message-sender-id'])
    assertEquals(cs.MT_NORMAL, sent_msg[0]['message-type'])
    assertContains('message-sent', sent_msg[0])
    assertEquals(msg[1], sent_msg[1])

    q.expect('dbus-return', method='SendMessage')

    call_async(q, conn, 'Disconnect')
    q.expect_many(
            EventPattern('dbus-signal', signal='StatusChanged', args=[2, cs.CSR_REQUESTED]),
            EventPattern('dbus-signal', signal='Closed', path=path),
            EventPattern('dbus-return', method='Disconnect'))

if __name__ == '__main__':
    exec_test(test)

