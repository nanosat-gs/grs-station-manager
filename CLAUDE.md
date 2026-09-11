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
