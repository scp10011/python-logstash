import os

try:
    from urllib import urlencode
except ImportError:
    from urllib.parse import urlencode

from logging import Filter
from logging.handlers import SocketHandler

try:
    import pika
except ImportError:
    pass
try:
    import kombu
except ImportError:
    pass

from logstash import formatter


class AMQPLogstashHandler(SocketHandler, object):
    """AMQP Log Format handler

    :param host: AMQP host (default 'localhost')
    :param port: AMQP port (default 5672)
    :param username: AMQP user name (default 'guest', which is the default for
        RabbitMQ)
    :param password: AMQP password (default 'guest', which is the default for
        RabbitMQ)

    :param exchange: AMQP exchange. Default 'logging.gelf'.
        A queue binding must be defined on the server to prevent
        log messages from being dropped.
    :param exchange_type: AMQP exchange type (default 'fanout').
    :param durable: AMQP exchange is durable (default False)
    :param virtual_host: AMQP virtual host (default '/').
    :param passive: exchange is declared passively, meaning that an error is
        raised if the exchange does not exist, and succeeds otherwise. This is
        useful if the user does not have configure permission on the exchange.

    :param tags: list of tags for a logger (default is None).
    :param message_type: The type of the message (default logstash).
    :param version: version of logstash event schema (default is 0).

    :param extra_fields: Send extra fields on the log record to graylog
        if true (the default)
    :param fqdn: Use fully qualified domain name of localhost as source
        host (socket.getfqdn()).
    :param facility: Replace facility with specified value. If specified,
        record.name will be passed as `logger` parameter.
    """

    def __init__(self, host='localhost', port=5672, username='guest',
                 password='guest', exchange='logstash', exchange_type='fanout',
                 virtual_host='/', message_type='logstash', tags=None, ssl_option=None,
                 durable=False, passive=False, version=0, extra_fields=True,
                 fqdn=False, facility=None, exchange_routing_key='', socket=None):
        # AMQP parameters
        self.host = host
        self.port = port
        self.ssl_option = ssl_option
        self.username = username
        self.password = password
        self.exchange_type = exchange_type
        self.exchange = exchange
        self.exchange_is_durable = durable
        self.declare_exchange_passively = passive
        self.virtual_host = virtual_host
        self.routing_key = exchange_routing_key
        self.socket = socket if socket in [KombuSocket, PikaSocket] else PikaSocket

        SocketHandler.__init__(self, host, port)

        # Extract Logstash paramaters
        self.tags = tags or []
        fn = formatter.LogstashFormatterVersion1 if version == 1 \
            else formatter.LogstashFormatterVersion0
        self.formatter = fn(message_type, tags, fqdn)

        # Standard logging parameters
        self.extra_fields = extra_fields
        self.fqdn = fqdn
        self.facility = facility

    def makeSocket(self, **kwargs):
        return self.socket(self.host,
                           self.port,
                           self.ssl_option,
                           self.username,
                           self.password,
                           self.virtual_host,
                           self.exchange,
                           self.routing_key,
                           self.exchange_is_durable,
                           self.declare_exchange_passively,
                           self.exchange_type)

    def makePickle(self, record):
        return self.formatter.format(record)


class KombuSocket(object):

    def __init__(self, host, port, ssl_option, username, password, virtual_host, exchange,
                 routing_key, durable, passive, exchange_type):
        self.connection = kombu.Connection(
            f'amqp://{username}:{password}@{host}:{port}/{virtual_host}', ssl=ssl_option)
        self.producer = self.connection.Producer(serializer='json')
        self.routing_key = routing_key
        self.exchange = exchange
        if not passive:
            exchange = kombu.Exchange(exchange, exchange_type, durable=durable)
            self.queue = [kombu.Queue('logstash', exchange=exchange, key=routing_key)]
        else:
            self.queue = None

    def sendall(self, data):
        self.producer.publish(data, exchange=self.exchange, routing_key=self.routing_key,
                              declare=self.queue, content_type='application/json')

    def close(self):
        try:
            self.producer.cancel()
        except Exception:
            pass


class PikaSocket(object):

    def __init__(self, host, port, ssl_option, username, password, virtual_host, exchange,
                 routing_key, durable, passive, exchange_type):
        if ssl_option:
            ssl_option = pika.SSLOptions(keyfile=ssl_option['keyfile'],
                                         certfile=ssl_option['certfile'],
                                         verify_mode=ssl_option['cert_reqs'],
                                         cafile=ssl_option['ca_certs'],
                                         server_hostname=os.getenv('HOSTNAME', 'localhost'))

        # create connection parameters
        credentials = pika.PlainCredentials(username, password)
        parameters = pika.ConnectionParameters(host, port, virtual_host,
                                               credentials, ssl_options=ssl_option)

        # create connection & channel
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

        # create an exchange, if needed
        self.channel.exchange_declare(exchange=exchange,
                                      exchange_type=exchange_type,
                                      passive=passive,
                                      durable=durable)

        # needed when publishing
        self.spec = pika.spec.BasicProperties(delivery_mode=2)
        self.routing_key = routing_key
        self.exchange = exchange

    def sendall(self, data):

        self.channel.basic_publish(self.exchange,
                                   self.routing_key,
                                   data,
                                   properties=self.spec)

    def close(self):
        try:
            self.connection.close()
        except Exception:
            pass
