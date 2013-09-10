import json
import socket
import logging
import asynchat
import threading
# Double-ended queue, thread-safe append/pop.
from collections import deque

logger = logging.getLogger("pymorse")
logger.setLevel(logging.WARNING)
# logger.addHandler( logging.NullHandler() )

MSG_SEPARATOR=b"\n"

class Stream(asynchat.async_chat):
    """ Asynchrone I/O stream handler

    To start the handler, just run :meth asyncore.loop: in a new thread::

    threading.Thread( target = asyncore.loop, kwargs = {'timeout': .1} ).start()

    where timeout is used with select.select / select.poll.poll.
    """
    def __init__(self, host='localhost', port='1234', maxlen=100, sock=None):
        self.error = False
        asynchat.async_chat.__init__(self, sock=sock)
        if not sock:
            self.create_socket(family=socket.AF_INET, type=socket.SOCK_STREAM)
            self.connect( (host, port) )
        self.set_terminator(MSG_SEPARATOR)
        self._in_buffer  = b""
        self._in_queue   = deque([], maxlen)
        self._callbacks  = []
        self._cv_new_msg = threading.Condition()

    def is_up(self):
        """
        self.connecting has been introduced lately in several branches
        of python (see issue #10340 of Python). In particular, it is not
        present in the python 3.2.3 interpreter delivered in Ubuntu 12.04.
        On this platform, just test of self.connected. There is still
        possibly a little race  but it mitigate the issue.
        """
        if hasattr(self, 'connecting'):
            return self.connecting or self.connected
        else:
            return self.connected

    def subscribe(self, callback):
        self._callbacks.append(callback)

    def unsubscribe(self, callback):
        self._callbacks.remove(callback)

    def handle_error(self):
        self.error = True
        self.handle_close()

    #### IN ####
    def collect_incoming_data(self, data):
        """Buffer the data"""
        self._in_buffer += data

    def found_terminator(self):
        self.handle_msg(self._in_buffer)
        self._in_buffer = b""

    def handle_msg(self, msg):
        """ append new raw :param msg: in the input queue

        and call subscribed callback methods if any
        """
        with self._cv_new_msg:
            self._in_queue.append(msg)
            self._cv_new_msg.notify_all()
        # handle callback(s)
        decoded_msg = None
        for callback in self._callbacks:
            if not decoded_msg:
                decoded_msg = self.decode( msg )
            callback( decoded_msg )

    def _msg_available(self):
        return bool(self._in_queue)

    def _get_last_msg(self):
        return self.decode( self._in_queue[-1] )

    # TODO implement last n msg decode and msg_queue with hash(msg) -> decoded msg
    def last(self, n=1):
        """ get the last message recieved

        :returns: decoded message or None if no message available
        """
        with self._cv_new_msg:
            if self._msg_available():
                return self._get_last_msg()
        logger.debug("last: no message in queue")
        return None

    def get(self, timeout=None):
        """ wait :param timeout: for a new messge

        When the timeout argument is present and not None, it should be a
        floating point number specifying a timeout for the operation in seconds
        (or fractions thereof).

        :returns: decoded message or None in case of timeout
        """
        with self._cv_new_msg:
            if self._cv_new_msg.wait(timeout):
                return self._get_last_msg()
        logger.debug("get: timed out")
        return None

    #### OUT ####
    def publish(self, msg):
        """ encode :param msg: and append the resulting bytes to the output queue """
        self.push(self.encode( msg ))

    #### patch code from asynchat, ``del deque[0]`` is not safe #####
    def initiate_send(self):
        while self.producer_fifo and self.connected:
            first = self.producer_fifo.popleft()
            # handle empty string/buffer or None entry
            if not first:
                if first is None:
                    self.handle_close()
                    return

            # handle classic producer behavior
            obs = self.ac_out_buffer_size
            try:
                data = first[:obs]
            except TypeError:
                data = first.more()
                if data:
                    self.producer_fifo.appendleft(data)
                continue

            if isinstance(data, str) and self.use_encoding:
                data = bytes(data, self.encoding)

            # send the data
            try:
                num_sent = self.send(data)
            except socket.error:
                self.handle_error()
                return

            if num_sent:
                if num_sent < len(data) or obs < len(first):
                    self.producer_fifo.appendleft(first[num_sent:])
            # we tried to send some actual data
            return


    #### CODEC ####
    def decode(self, msg_bytes):
        """ decode bytes to string """
        return msg_bytes.decode()

    def encode(self, msg_str):
        """ encode string to bytes """
        return msg_str.encode() + MSG_SEPARATOR



class StreamJSON(Stream):
    def __init__(self, host='localhost', port='1234', maxlen=100, sock=None):
        Stream.__init__(self, host, port, maxlen, sock)

    def decode(self, msg_bytes):
        """ decode bytes to json object """
        return json.loads(Stream.decode(self, msg_bytes))

    def encode(self, msg_obj):
        """ encode object to json string and then bytes """
        return Stream.encode(self, json.dumps(msg_obj))
