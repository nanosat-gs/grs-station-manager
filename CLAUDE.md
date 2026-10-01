# Station Manager (`mgm8`)

**Este repo é o Station Manager, não o GRS Manager.** Os nomes são parecidos e
os serviços são diferentes:

- **Station Manager** (aqui): controla o rotor e conduz sozinho uma passagem do
  AOS ao LOS. Fala **só ZMQ**, na 5580. Não tem HTTP, não tem banco.
- **GRS Manager** ([nanosat-gs/grs-manager](https://github.com/nanosat-gs/grs-manager)):
  painel do operador e ponte rotctld. É *cliente* deste serviço.

## Portas

| Porta | Protocolo | Direção |
|---|---|---|
| 5580 | ZMQ REP (JSON) | **expõe** — quem pede: GRS Manager e TC Scheduler |
| 5559 / 5560 | ZMQ PUSH / SUB (Rot2Prog binário) | **consome** — o Rotor Manager |
| 5581 | ZMQ PUB (`freq` / `doppler`, por rádio) | **anuncia** — quem ouve: um `grs-frequency-synthesizer` por rádio |

### Anúncio de sintonia (5581)

Só existe com `--tuning-bind`. Omitido, o serviço não anuncia nada, que é o
padrão de uma estação sem caminho de recepção.

    [b"freq",    b"<Hz>"]   portadora NOMINAL, uma vez por passagem
    [b"doppler", b"<Hz>"]   desvio do instante, a cada tick de apontamento

O sintetizador soma as duas e publica a frequência efetiva na :5557, de onde o
receptor de IQ (retune por hardware) ou o demodulador (correção digital) a
consomem — **a mesma mensagem serve aos dois**; qual deles age é configuração.

**Mais de um rádio** (`--radios vhf=143000000-148000000,uhf=...`, ou a variável
`STATION_RADIOS`): o `track_satellite` traz uma lista de `downlinks`
(`[{name, frequency_hz}]`), cada um vai ao rádio cuja faixa o contém
(`domain.models.route_downlinks`) e é anunciado no canal dele:
`freq.vhf`/`doppler.vhf`, `freq.uhf`/... Um downlink sem rádio, ou um segundo
na mesma faixa, fica de fora com aviso no log. Sem rádios declarados, só o
primeiro downlink é anunciado, nos tópicos sem canal — o comportamento antigo.

O Doppler de referência (o primeiro downlink roteado) vem da
spacelab-tracking; os outros saem por proporção (o desvio é f·v/c). E é
calculado meio intervalo à frente do tick (`doppler_lead_seconds`, que o
`main` põe em intervalo/2): o receptor fica com a sintonia de um tick até o
próximo, e no meio desse intervalo o erro de dente de serra cai pela metade.

5581, e não 5559: a 5559 já é do Rotor Manager.

## O protocolo da 5580 é definido aqui

O docstring de [`src/mgm8/rotor_zmq/server.py`](src/mgm8/rotor_zmq/server.py) é
a **fonte canônica** do protocolo. Os clientes (GRS Manager, TC Scheduler) o
referenciam por URL; não copiem o schema para lá, porque três cópias em três
repositórios divergem de verdade.

Comandos manuais: `set_target`, `get_position`, `stop`, `park`.
Comandos autônomos: `track_satellite`, `stop_tracking`, `get_tracking`.

**`track_satellite` carrega `OrbitalData.to_json()`** da `spacelab-tracking`.
Esse é o acoplamento mais delicado do sistema depois do split: este repo e o
`grs-tc-scheduler` pinam tags **independentes** da biblioteca, e uma mudança
no formato produz falha de desserialização no meio de uma passagem. Subir a tag
aqui sem subir lá (ou vice-versa) é a forma mais fácil de quebrar a estação.

## Arquitetura

Hexagonal. Portas em `domain/ports.py`, adapters em `infrastructure/`, casos de
uso em `application/`. A composition root (`rotor_zmq/main.py`) é o único
módulo que conhece implementações concretas.

**O laço de apontamento em tempo real roda aqui, não no Scheduler.** Planejar é
lento (propagar 24h de N satélites) e apontar é rápido. Separados, um
replanejamento nunca atrasa o rotor, e uma queda do Scheduler no meio de uma
passagem não a interrompe. O Scheduler manda **uma ordem por passagem**, não um
setpoint por segundo.

## Armadilhas

- **O `[doppler]` sai com o satélite abaixo do horizonte também.** A elevação
  mínima (`--pointing-min-elevation`) só decide se o ROTOR se move; a
  sintonia é anunciada a cada tick da passagem.
- **`RotorZmqServer.close()` espera o laço sair antes de fechar o socket.**
  Fechar um socket ZMQ de outra thread enquanto o `recv` está em curso fazia
  o libzmq ABORTAR o processo (assertion, não exceção) — a suíte caía ao
  acaso, uma vez em duas.

- **`freq` e `doppler` são duas mensagens, não uma soma.** Quem consome precisa
  distinguir "trocou de satélite" (salto de dezenas de MHz) de "o satélite se
  moveu" (alguns kHz). Mandar só a soma faria os dois chegarem indistinguíveis,
  e o receptor não teria como decidir entre re-sintonizar o hardware e corrigir
  em software.
- **Doppler ausente é `None`, nunca `0.0`.** Zero afirmaria "sem desvio", o que
  mandaria sintonizar na nominal justamente no meio da passagem, onde o desvio é
  maior. Ausência de dado e ausência de desvio não podem ter a mesma
  representação.
- **PUB descarta o que publica sem assinante conectado.** Um sintetizador que
  sobe no meio de uma passagem perderia o `freq` que a abriu e ficaria sem
  referência. Por isso `ZmqTuningBroadcast` reenvia a nominal a cada 30 anúncios
  de Doppler.
- **A frequência do satélite vem no payload do `track_satellite`, não do
  banco.** Este serviço não conhece o Postgres — quem lê é o TC Scheduler, que
  já monta a ordem de rastreamento. Sem a frequência a passagem é rastreada
  igual, só sem anúncio de sintonia.
- **`NullTuningBroadcast` mora no domínio, não ao lado do adapter ZMQ.** A
  camada de aplicação não pode importar infraestrutura, e importá-lo de lá
  arrastaria o pyzmq — que aqui é dependência OPCIONAL.

- **`--rotor mock` no Docker.** O `RotorManager` (em `src/mgm8/vendor/`) tem o
  socket SUB de status fixo em `tcp://localhost:5560`, o que não funciona entre
  containers. Hardware real e simulador continuam fora do Docker. A cópia está
  sob nosso controle (ver `src/mgm8/vendor/UPSTREAM.md`), então parametrizar
  esse endereço é uma mudança possível — só não foi feita junto da cópia.
- **`src/mgm8/vendor/` é código de terceiros copiado**, não escrito aqui. Antes
  era um submódulo alcançado por `sys.path.insert`, que quebrava assim que o
  pacote mudasse de lugar. Ver `UPSTREAM.md` para origem e commit.
- **A tag da `spacelab-tracking` está em dois lugares**: `pyproject.toml` e o
  `ARG TRACKING_REF` do Dockerfile. Subir uma sem a outra faz o build instalar
  uma versão e os testes locais outra.

## Rodar

```bash
pip install -e ".[dev]"
pytest

python -m mgm8.rotor_zmq.main --bind=tcp://0.0.0.0:5580 --rotor=mock
```

Contra o simulador de rotor, em dois terminais e **fora do Docker**:

```bash
python tools/rotor_simulator.py
python -m mgm8.rotor_zmq.main --rotor=zmq --rotor-address=tcp://127.0.0.1:5559
```
