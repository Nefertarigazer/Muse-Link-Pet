"""Render the contract's confirmation chime, 44.1 kHz mono signed PCM16."""
import argparse
import array
import json
import math
from pathlib import Path
import sys
import wave

HERE = Path(__file__).resolve().parent


def render(path, contract=HERE / 'ui/fixtures/contract.json'):
    spec = json.loads(Path(contract).read_text(encoding='utf-8'))['confirm_chime']
    rate = 44100
    duration = max(note['at'] for note in spec['notes']) + spec['decay'] + .05
    samples = array.array('h')
    for i in range(math.ceil(duration * rate)):
        value = 0.
        for note in spec['notes']:
            t = i / rate - note['at']
            if 0 <= t <= spec['decay']:
                envelope = t / spec['attack'] if t < spec['attack'] else math.exp(
                    math.log(.0001) * (t - spec['attack']) / (spec['decay'] - spec['attack']))
                phase = 2 * math.pi * note['hz'] * t
                value += spec['gain'] * envelope * (math.sin(phase) + spec['overtone'] * math.sin(phase * 2))
        samples.append(round(max(-1, min(1, value)) * 32767))
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(samples.tobytes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=HERE / 'pet-confirm.wav')
    render(parser.parse_args().output)
