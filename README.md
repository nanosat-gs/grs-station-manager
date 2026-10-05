# Station Manager (`mgm8`)

Controla o rotor da estação terrestre do SpaceLab e conduz sozinho uma passagem
de satélite, do AOS ao LOS: aponta a antena e diz a cada rádio onde sintonizar.

Recebe **uma ordem por passagem** (`track_satellite`, com os elementos orbitais,
a hora do LOS e os downlinks do satélite) e a partir daí propaga, calcula o
apontamento, comanda o rotor e anuncia a sintonia sem depender de mais ninguém.
É por isso que uma queda do TC Scheduler no meio de uma passagem não a
interrompe.

Faz parte da estação terrestre orquestrada em
[nanosat-gs/grs-station](https://github.com/nanosat-gs/grs-station).

```
TC Scheduler ──track_satellite──▶ ┌─────────────────┐ ──Rot2Prog──▶ rotor
GRS Manager ───set_target/get_──▶ │ Station Manager │
Spectrum Monitor ─get_tracking──▶ │      :5580      │ ──:5581 freq/doppler/offset.<rádio>──▶ grs-frequency-synthesizer (um por rádio)
                                  │                 │ ◀─:5582 afc.<rádio>── grs-fft (um por rádio)
                                  └─────────────────┘ ──:5583 fft.* (repasse)──▶ grs-spectrum-monitor
```

## Interfaces

| Porta | Tipo | O quê | Liga com |
|---|---|---|---|
| 5580 | ZMQ REP, JSON | Comandos de rotor e de rastreamento | sempre |
| 5581 | ZMQ PUB | Sintonia por rádio: `freq.<rádio>`, `doppler.<rádio>`, `offset.<rádio>` | `--tuning-bind` |
| 5582 | ZMQ SUB (consome) | Medidas dos blocos FFT: `afc.<rádio>` | `--fft-sources` |
| 5583 | ZMQ XPUB | Repasse do espectro dos blocos FFT (`fft.<rádio>`) | `--spectrum-bind` |
| 5559 / 5560 | ZMQ PUSH / SUB (consome) | Rotor Manager (Rot2Prog) | `--rotor zmq` |

Sem HTTP e sem banco, de propósito: o serviço precisa poder rodar na máquina do
rádio, longe do resto da estação.

### 5580 — comandos

O protocolo está documentado no docstring de
[`src/mgm8/rotor_zmq/server.py`](src/mgm8/rotor_zmq/server.py), que é a fonte
canônica para os clientes.

```json
{"cmd": "set_target", "azimuth_degrees": 180.0, "elevation_degrees": 45.0}
{"cmd": "track_satellite", "orbital_data": {...}, "until": "2026-08-27T14:30:00+00:00",
 "satellite_name": "FS-2",
 "downlinks": [{"name": "beacon", "frequency_hz": 145900000},
               {"name": "dados",  "frequency_hz": 468400000}]}
{"cmd": "get_tracking"}
```

Manuais: `set_target`, `get_position`, `stop`, `park`. Autônomos:
`track_satellite`, `stop_tracking`, `get_tracking`. O `get_tracking` devolve,
por downlink, o rádio, o Doppler do instante e o estado do ajuste fino
(`offset_hz`, `afc_updates`, `afc_last_residual_hz`) — é dali que o TC
Scheduler guarda o erro de oscilador aprendido e o Spectrum Monitor tira o
contexto da passagem.

### 5581 — sintonia

Cada downlink vai ao rádio cuja faixa o contém (`--radios` /
`STATION_RADIOS`) e é anunciado no canal dele:

    [b"freq.vhf",    b"145900000"]   portadora nominal, por passagem (reenviada a cada 30 ticks)
    [b"doppler.vhf", b"-2310"]       desvio previsto, a cada tick
    [b"offset.vhf",  b"1196"]        ajuste fino (AFC), a cada tick

O `grs-frequency-synthesizer` daquele rádio soma os três e publica o `tune`.
Sem rádios declarados, só o primeiro downlink é anunciado, nos tópicos sem
canal (`freq`, `doppler`). Sem downlink cadastrado, o rotor segue a passagem
mas nada sai na 5581.

O Doppler do primeiro downlink vem da `spacelab-tracking`; os outros, por
proporção (f·v/c). Ele é calculado para o **meio do intervalo** entre dois
ajustes, o que corta pela metade o erro de dente de serra.

### Ajuste fino (AFC)

O Doppler previsto não vê o erro do oscilador do satélite. Com
`--fft-sources`, o Station Manager assina as medidas dos blocos `grs-fft` (a
que distância do centro cada rajada chegou) e as integra num desvio por rádio:
ganho 0,5, passo de no máximo 2 kHz, total de no máximo 8 kHz (`--afc-*`), só
durante a passagem e no rádio que ela usa. Cada passagem começa do zero. Ver o
`CLAUDE.md` para as regras completas.

## Instalar e rodar

```bash
pip install -e ".[dev]"
pytest

# só o rotor
python -m mgm8.rotor_zmq.main --bind=tcp://0.0.0.0:5580 --rotor=mock

# como na estação: dois rádios, sintonia, ajuste fino e repasse do espectro
python -m mgm8.rotor_zmq.main --bind=tcp://0.0.0.0:5580 --rotor=mock \
    --radios vhf=143000000-148000000,uhf=462000000-470000000 \
    --tuning-bind tcp://0.0.0.0:5581 \
    --fft-sources vhf=tcp://172.30.0.24:5582,uhf=tcp://172.30.0.34:5582 \
    --spectrum-bind tcp://0.0.0.0:5583
```

As fontes FFT vão por IP, não por nome (ver `CLAUDE.md`). Os IPs acima são os
da estação; o mapa completo está no fim do `docker-compose.yml` do
orquestrador.

`--rotor zmq` fala com o rotor físico ou com `tools/rotor_simulator.py`. Isso
não funciona dentro do Docker — ver a seção de armadilhas no `CLAUDE.md`.

### Configuração

| Argumento | Variável | Padrão |
|---|---|---|
| `--gs-name`, `--gs-latitude`, `--gs-longitude`, `--gs-altitude` | `GS_*` | São Paulo (exemplo) |
| `--pointing-interval` | `STATION_POINTING_INTERVAL_SECONDS` (no compose) | 1 s |
| `--pointing-min-elevation` | `STATION_POINTING_MIN_ELEVATION` (no compose) | 0° |
| `--radios` | `STATION_RADIOS` | vazio: um canal só |
| `--fft-sources` | `STATION_FFT_SOURCES` | vazio: sem ajuste fino |
| `--afc-gain` / `--afc-max-step` / `--afc-max-offset` | — | 0,5 / 2000 Hz / 8000 Hz |

As `GS_*` precisam bater com as do TC Scheduler, ou o plano e o apontamento
divergem sobre de onde a estação observa.

## Estrutura

```
src/mgm8/
├── application/     # Casos de uso: controle de rotor e rastreamento de passagem
├── domain/          # Entidades, value objects, portas; roteamento de downlinks e passo do AFC
├── infrastructure/  # Adapters de saída: rotor mock/ZMQ, apontamento SGP4,
│                    # anúncio de sintonia (5581), escuta AFC e repasse do espectro
├── vendor/          # Rotor Manager copiado do grs-rotor-manager (ver UPSTREAM.md)
└── rotor_zmq/       # Adapter de entrada ZMQ e composition root
```
