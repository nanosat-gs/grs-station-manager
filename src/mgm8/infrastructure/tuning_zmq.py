"""Adapter de saída: anuncia frequência e Doppler num PUB ZMQ.

Implementa TuningBroadcast. Do outro lado fica o `grs-frequency-synthesizer`,
que assina os dois tópicos, soma, e publica a frequência efetiva na :5557 —
de onde o receptor de IQ (retune por hardware) ou o demodulador (correção
digital) a consomem.

Contrato, definido pelo sintetizador e espelhado aqui:

    [b"freq",    b"<Hz em ASCII>"]   portadora nominal, uma vez por passagem
    [b"doppler", b"<Hz em ASCII>"]   desvio do instante, a cada tick

Com mais de um rádio, cada um tem o seu canal, e o tópico leva o nome dele:
`freq.vhf`, `doppler.vhf`, `freq.uhf`... O sintetizador de cada rádio assina
só o seu (`--channel`). Cuidado com o prefixo do ZMQ: assinar `freq` também
recebe `freq.vhf` — quem assina sem canal tem de comparar o tópico inteiro.

PUB e não REQ/REP: anunciar sintonia é difusão, não conversa. Ninguém responde,
e o Station Manager não pode ficar esperando resposta no meio do laço que
comanda o rotor — um sintetizador fora do ar não pode custar o apontamento.

A consequência a conhecer: PUB descarta o que publica enquanto não há
assinante conectado. Um sintetizador que sobe no meio de uma passagem perde o
`freq` que abriu a passagem e fica sem referência — por isso `announce_doppler`
reenvia a frequência quando ela ainda não foi aceita. Ver o reenvio abaixo.
"""

from __future__ import annotations

import logging

import zmq

logger = logging.getLogger(__name__)

# 5581, e não 5559: a 5559 já é do RotorManager (ver DEFAULT_ROTOR_ADDRESS em
# rotor_zmq/main.py). A porta fica na família do próprio Station Manager, ao
# lado do REP em 5580.
DEFAULT_TUNING_BIND_ADDRESS = "tcp://0.0.0.0:5581"

# A cada quantos anúncios de Doppler a frequência nominal é repetida. A 1 Hz de
# laço, 30 significa uma repetição a cada 30 s — barato, e resolve o assinante
# que conectou depois do começo da passagem.
FREQUENCY_REPEAT_EVERY = 30


class ZmqTuningBroadcast:
    """Implementa TuningBroadcast publicando em ZMQ."""

    def __init__(self, bind_address: str = DEFAULT_TUNING_BIND_ADDRESS) -> None:
        # Context dedicado, pelo mesmo motivo do RotorZmqServer: instabilidade
        # observada no libzmq no Windows com muitos sockets disputando o
        # context global.
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.PUB)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(bind_address)

        # Por canal: a última nominal e quantos Doppler saíram desde ela.
        self._last_frequency_hz: dict[str | None, float] = {}
        self._doppler_since_frequency: dict[str | None, int] = {}

        logger.info("Anúncios de sintonia em %s", bind_address)

    def announce_frequency(self, hz: float, channel: str | None = None) -> None:
        self._last_frequency_hz[channel] = hz
        self._doppler_since_frequency[channel] = 0
        self._send(_topic(b"freq", channel), hz)

    def announce_doppler(self, hz: float, channel: str | None = None) -> None:
        # Reenvia a nominal de tempos em tempos: um assinante que conectou
        # depois do início da passagem nunca viu o `freq`, e o sintetizador
        # recusa Doppler sem frequência de referência. Sem isto, subir o
        # sintetizador no meio de uma passagem significa perder a passagem.
        nominal = self._last_frequency_hz.get(channel)
        if nominal is not None:
            if self._doppler_since_frequency.get(channel, 0) >= FREQUENCY_REPEAT_EVERY:
                self._send(_topic(b"freq", channel), nominal)
                self._doppler_since_frequency[channel] = 0
            else:
                self._doppler_since_frequency[channel] = (
                    self._doppler_since_frequency.get(channel, 0) + 1)

        self._send(_topic(b"doppler", channel), hz)

    def _send(self, topic: bytes, hz: float) -> None:
        try:
            # Inteiro em ASCII: é o que o sintetizador lê (int(float(...))).
            # Hz inteiro é resolução de sobra — o Doppler de um LEO em VHF
            # varia alguns quilohertz ao longo de minutos.
            self._socket.send_multipart([topic, str(int(round(hz))).encode()], zmq.NOBLOCK)
        except zmq.ZMQError:
            # Falha ao publicar não pode derrubar o laço de apontamento. A
            # antena continua seguindo o satélite mesmo que ninguém esteja
            # ouvindo o anúncio de sintonia.
            logger.exception("Falha ao publicar %s", topic.decode())

    def close(self) -> None:
        self._socket.close()
        self._context.term()


def _topic(base: bytes, channel: str | None) -> bytes:
    return base if channel is None else base + b"." + channel.encode()
