import os
import sys
import time
import shutil
import subprocess
import threading
import traceback
import math

def pip_import(module, pipname=None):
    pipname = pipname or module
    try:
        globals()[module] = __import__(module)
    except ImportError:
        print("ERROR: could not load module " + module + " [" + pipname + "]")
        print("you need to install it yourself by running:")
        print("python -m pip install " + pipname)
        input()

pip_import("mido")
pip_import("requests")

class PathManager:
    def __init__(self, root=None):
        self.base_location = root or os.path.dirname(os.path.realpath(__file__))

    def get_path(self, path, *args):
        if ''.join(path if isinstance(path, list) else [path]).startswith('$'):
            return os.path.join(self.base_location, *([path.split('/')[0][1:]] + path.split('/')[1:] + list(args)))
        return os.path.join(*path.split('/') + list(args))

    def assert_directory(self, path):
        path = self.get_path(path)
        if (not os.path.isdir(path)):
            os.makedirs(path)

    def default_file(self, path, content):
        path = self.get_path(path)
        self.assert_directory(os.path.dirname(path))
        if not os.path.isfile(path):
            file = open(path, 'w')
            file.write(content)
            file.close()

    def get_json(self, path, mode='r'):
        file = open(self.get_path(path), mode)
        content = json.loads(file.read())
        file.close()
        return content

    def set_json(self, path, content, mode='w'):
        output = json.dumps(content)
        file = open(self.get_path(path), mode)
        file.write(output)
        file.close()

    def read_file(self, path, mode='r'):
        file = open(self.get_path(path), mode)
        content = file.read()
        file.close()
        return content        

    def set_file(self, path, content, mode='w'):
        file = open(self.get_path(path), mode)
        file.write(content)
        file.close()


class FilePathInputManager:
    def __init__(self):
        self.tkinter = None
        self.filedialog = None
        try:
            self.tkinter = __import__("tkinter")
            self.filedialog = __import__("tkinter.filedialog")
        except:
            pass

    def get(self):
        if self.tkinter == None or self.filedialog == None:
            return self.get_fallback()
        self.tk = self.tkinter.Tk()
        self.tk.withdraw()
        while True:
            file = self.tkinter.filedialog.askopenfilename(filetypes=[('MIDI Files', ('.midi', '.mid'))])
            if (file != None):
                self.tk.destroy()
                return file

    def get_fallback(self):
        print("enter a file path")
        while True:
            file = input()
            if os.path.isfile(file):
                break
        return file


class MidiTranslationManager:
    blocks = {
        "bass": "oak_planks",
        "snare": "sand",
        "hat": "glass",
        "basedrum": "stone",
        "bell": "gold_block",
        "flute": "clay",
        "chime": "packed_ice",
        "guitar": "white_wool",
        "xylophone": "bone_block",
        "piano": "iron_block"
    }

    midi = {
        "0,1,2,3,4,5,6": "piano",
        "7,8": "guitar",
        "9,10,11,12": "chime",
        "13,14": "xylophone",
        "15": "bell",
        "16": "guitar",
        "25,26,27,28,29,30,31,21": "guitar",
        "33,34,35,36": "bass",
        "37,38": "basedrum",
        "39,40": "bass",
        "113": "bell",
        "114": "hat",
        "115": "basedrum",
        "116": "hat",
        "117": "snare",
        "118": "basedrum",
        "119": "snare"
    }

    def get_instrument(instrument):
        instrument = str(instrument)
        for x in MidiTranslationManager.midi:
            if x == instrument or ("," + instrument + ",") in x or x.startswith(instrument + ",") or x.endswith("," + instrument):
                return MidiTranslationManager.midi[x]
        return "piano"

    def get_block(instrument):
        return MidiTranslationManager.blocks[MidiTranslationManager.get_instrument(instrument)]


    def note_block_pitch(midipitch):
        pitch = midipitch - 54
        while pitch < 0:
            pitch += 12
        while pitch > 24:
            pitch -= 12
        return pitch

class NoteBlockMessage:
    def __init__(self, note, instrument, leading_delay, delay):
        self.instrument = instrument
        self.note = note
        self.leading_delay = leading_delay
        self.delay = delay


class NoteBlockConverter:
    def __init__(self, fp):
        self.midi = mido.MidiFile(fp)
        self.midi_messages = []
        self.noteblock = []
        self.tempo_modifier = 1.0

    def extract_messages(self):
        for message in self.midi:
            self.midi_messages.append(message)

    def generate_noteblock_objects(self):
        channel_instrument = {}
        total_delay = 0.0
        output = []
        for message in self.midi_messages:
            if message.is_meta:
                continue
            if message.type == "program_change":
                channel_instrument[message.channel] = message.program
            if message.type in ["note_on", "note_off"]:
                instrument = channel_instrument[message.channel] if message.channel in channel_instrument else 0
                output.append(NoteBlockMessage(message.note if message.type == "note_on" else None, instrument, total_delay, message.time))
            try:
                total_delay += message.time / self.tempo_modifier
            except:
                pass
        block_groups = [[]]
        for message in output:
            if len(block_groups[-1]) != 0:
                if message.leading_delay != block_groups[-1][-1].leading_delay:
                    block_groups.append([])
            block_groups[-1].append(message)
        self.noteblock = block_groups


class NoteBlockLane:
    def __init__(self):
        self.objects = []

    def add_repeater(self, ticks): 
        add = ticks
        if len(self.objects) > 0: 
            if self.objects[-1][0] == "repeater": # stack up ticks instead of adding a bunch of 1 tick repeaters
                total = self.objects[-1][1] + ticks
                self.objects[-1][1] = min([total, 4])
                add = max([total - 4, 0])

        if add == 0:
            return
        self.objects.append(["repeater", add])

    def add_blocks(self, blocks):
        self.objects.append(["blocks", blocks])

    def add_stud(self):
        self.objects.append(["stud", None])


class NoteBlockStructureGenerator:
    def __init__(self, noteblockmessages):
        self.messages = noteblockmessages
        self.structures = []
        self.command_delay = 0.0
        self.server_instance = None
        self.facing = {
            0: "south",
            1: "west",
            2: "north",
            3: "east"
        }

    def generate(self):
        biggest_frame = max([len(x) for x in self.messages])
        lanes = [NoteBlockLane() for x in range(0, math.ceil(biggest_frame / 3))]
        time = -0.1
        current_items = [item for sublist in self.messages.copy() for item in sublist]
        current_items = [item for item in current_items if item.note != None]
        max_time = max([item.leading_delay for item in current_items])
        while not (time > max_time):
            time += 0.1
            tick = []
            for x in current_items:
                if time > x.leading_delay and x.note != None:
                    tick.append(x)
            lane_number = 0
            notes_lanes = [tick[x:x+3] for x in range(0, len(tick), 3)]
            if len(notes_lanes) != 0:
                for x in range(0, len(lanes)):
                    if x >= len(notes_lanes):
                        lanes[x].add_stud()
                        continue
                    lanes[x].add_blocks(notes_lanes[x])
            for x in tick:
                current_items.remove(x)
            for x in lanes:
                x.add_repeater(1)

        self.structures = lanes

    def place_block(self, x, y, z, block):
        #print("setblock %s %s %s %s" % (x, y, z, block))
        self.server_instance.send_command("setblock %s %s %s %s" % (x, y, z, block))
        time.sleep(self.command_delay)
            
    def build(self, server_instance, x_pos, y_pos, z_pos, direction):
        self.server_instance = server_instance
        forward_x = 0 if direction % 2 == 0 else direction - 2
        forward_z = 0 if direction % 2 != 0 else 2 - direction - 1
        sideways_x = 0 - forward_z
        sideways_z = 0 - forward_x
        
        max_entries = max([len(x.objects) for x in self.structures])
        for x in range(0, max_entries):
            current_x = x_pos + forward_x * x
            current_z = z_pos + forward_z * x
            for y in range(0, len(self.structures)):
                lane = self.structures[y]
                lane_x = current_x + sideways_x * 3 * y
                lane_z = current_z + sideways_z * 3 * y
                if x < len(lane.objects):
                    item = lane.objects[x]
                    if (item[0] == "repeater"):
                        self.place_block(lane_x, y_pos + 1, lane_z, "iron_block")
                        self.place_block(lane_x, y_pos + 2, lane_z, "repeater[facing=%s,delay=%s]" % (self.facing[direction], item[1]))
                    if (item[0] == "stud"):
                        self.place_block(lane_x, y_pos + 2, lane_z, "iron_block")
                    if (item[0] == "blocks"):
                        start_x = lane_x
                        start_z = lane_z
                        if len(item[1]) > 1:
                            start_x = start_x + sideways_x * -1
                            start_z = start_z + sideways_z * -1
                        for z in item[1]:
                            pitch = MidiTranslationManager.note_block_pitch(z.note)
                            material = MidiTranslationManager.get_block(z.instrument)
                            if material in ["sand", "gravel"]:
                                self.place_block(start_x, y_pos, start_z, "iron_block")
                            self.place_block(start_x, y_pos + 1, start_z, material)
                            inst = MidiTranslationManager.get_instrument(z.instrument)
                            self.place_block(start_x, y_pos + 2, start_z, "note_block[note=" + str(pitch) + ("," + "instrument=" + inst if inst != "piano" else "") + "]")
                            start_x = start_x + sideways_x 
                            start_z = start_z + sideways_z


class MinecraftServerWrapper:
    def __init__(self):
        self.path_manager = PathManager()
        self.server_process = None
        self.server_logs = []
        self.output_thread = None
        self.remake_flat = False
        self.server_ready = False
        self.logging_paused = False
        self._logging_paused = False
        self.logging_disabled = False
        self.pause_queue = []
        if not os.path.isfile(self.path_manager.get_path("$minecraft_server_1.13.1.jar")):
            print("[s] downloading minecraft server...")
            server_jar = requests.get(r"https://launcher.mojang.com/v1/objects/fe123682e9cb30031eae351764f653500b7396c9/server.jar")
            if server_jar.status_code == 200:
                server_file = open(self.path_manager.get_path("$minecraft_server_1.13.1.jar"), "wb")
                server_file.write(server_jar.content)
                server_file.close()
                print("[s] done!")
            else:
                print("[s] error: bad response")
        eula_file = open(self.path_manager.get_path("$eula.txt"), "w")
        eula_file.write("eula=true")
        eula_file.close()

    def server_output_thread(self):
        while self.server_process == None:
            time.sleep(1)
        for line in iter(self.server_process.stdout.readline, b''):
            if self.logging_paused and not self._logging_paused:
                self.pause_queue = []
                self._logging_paused = True
            if not self.logging_paused and self._logging_paused:
                self._logging_paused = False
                if not self.logging_disabled:              
                    for x in self.pause_queue:
                        self.server_logs.append(x)
                        self.on_server_log(x)
            if not self.logging_disabled:              
                self.server_logs.append(line)
                self.on_server_log(line)
        self.on_server_close()

    def send_command(self, text):
        self.server_process.stdin.writelines([text.encode() + b'\r'])
        self.server_process.stdin.flush()

    def get_log_output(self, text, level=True):
        return text[(11 if level else 33):].strip('\n').replace('\r', '')
        
    def on_server_log(self, text):
        self.log_event(self, text)
        compare_text = self.get_log_output(text.decode(), False)
        if compare_text.startswith('Done (') and compare_text.endswith(')! For help, type "help"'):
            propreties = open(self.path_manager.get_path("$server.properties"), "r")
            is_flat = True in [False if (x.startswith('#') or x.strip() == "") else (False if x.split('=', 1)[0] != "level-type" else (False if x.split('=', 1)[1].lower() == "flat" else True)) for x in propreties.readlines()]
            propreties.close()
            if not is_flat:
                print('[s] world is not flat type. fixing..')
                self.remake_flat = True
                self.send_command("stop")
            else:
                self.server_ready = True

    def log_event(self, me, text):
        print(text.decode(), end="")
        sys.stdout.flush()

    def on_server_close(self):
        print('[s] server closed!')
        if self.remake_flat == True:
            time.sleep(2)
            self.server_process.terminate()
            shutil.rmtree(self.path_manager.get_path("$world"))
            propreties = open(self.path_manager.get_path("$server.properties"), "r")
            lines = propreties.readlines()
            propreties.close()
            propreties = open(self.path_manager.get_path("$server.properties"), "w")
            propreties.writelines([x if (x.startswith('#') or x.strip() == "") else (x if x.split('=', 1)[0] != "level-type" else ("level-type=FLAT\n")) for x in lines])
            propreties.close()
            self.start_server()
        

    def start_server(self):
        self.server_process = None
        self.server_logs = []
        self.output_thread = None
        self.remake_flat = False
        self.server_ready = False
        if not os.path.isfile(self.path_manager.get_path("$minecraft_server_1.13.1.jar")):
            return
        self.output_thread = threading.Thread(target=self.server_output_thread)
        self.output_thread.start()
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        print('[s] starting server..')
        self.server_process = subprocess.Popen(["java", "-Xmx1G", "-Xms1G", "-jar", self.path_manager.get_path("$minecraft_server_1.13.1.jar"), "nogui"], stdout=subprocess.PIPE, stdin=subprocess.PIPE, startupinfo=startup)



class NoteblockerCI:
    def __init__(self):
        self.minecraft_server = MinecraftServerWrapper()
        self.minecraft_server.log_event = self.log_event
        self.repeaterfix = True
        self.pythonw = "pythonw" in os.path.split(sys.executable)[1]
        self.tempo_modifier = 1.0
        self.facing_repeaterfix = {
            0: "north",
            1: "east",
            2: "south",
            3: "west"
        }

    def log_event(self, me, text):
        if len(me.server_logs) == 0:
            print()
        print(text.decode(), end="")
        sys.stdout.flush()

    def ready_server(self):
        self.minecraft_server.start_server()
        print('waiting for server', end="")
        while True:
            if len(self.minecraft_server.server_logs) == 0:
                print(".", end="")
                time.sleep(1)
            if self.minecraft_server.server_ready:
                break

    def try_get_arg(self, argslist, index, atype):
        try:
            return atype(argslist[index])
        except:
            return None

    def input_if_none(self, arg, argname, reqtype):
        if arg != None:
            return arg
        return self.input_arg(argname, reqtype)

    def input_arg(self, name, reqtype):
        try:
            typename = type(reqtype()).__name__
        except:
            typename = str(reqtype)
        while True:
            a = input("enter " + str(name) + ": ")
            try:
                return reqtype(a)
            except:
                print("please enter a" + ("n" if typename[0] in "aeiou" else "") + " " + typename.upper())

    def console(self):
        if self.pythonw:
            print("warning: console inputs do not work properly in idle or other windowed environments. disabling server log.")
            self.minecraft_server.logging_disabled = True
        print('welcome to the noteblocker console! for help try ?')
        while True:
            try:
                print('> ', end="")
                sys.stdout.flush()
                q = sys.stdin.readline()
                sys.stdout.flush()
            except KeyboardInterrupt:
                break
            try:
                self.process_command(q)
            except KeyboardInterrupt:
                continue
            except BaseException as e:
                print('an error occurred while executing this command')
                print("\n".join(traceback.format_exception(type(e), e, e.__traceback__)))
        print('kthxbai')
        try:
            self.minecraft_server.send_command("")
            self.minecraft_server.send_command("stop")
        except:
            pass

    def process_command(self, q):
        if q.strip() == "":
            return
        command = q.strip().split()
        if q.strip() == "?":
            print("/command - starting a command with / executes a minecraft server side command e.g. /op <player>")
            print("nbgen (x) (y) (z) (direction - north/east/south/west) - generates a noteblock sequence. a path will be prompted later. you can if you want but do not need to provide coords/direction. make sure that area of the world is loaded!")
            print("repeaterfix <on/off> - in 1.13.1 there is a bug that causes repeaters to place facing the wrong direction. this toggles a fix for this. [on by default]")
            print("tempomod (float) - edits the tempo modifier [default 1.0]")
        if q.strip().startswith('/'):
            self.minecraft_server.send_command(q.strip()[1:])
        if command[0] == "repeaterfix":
            on = self.try_get_arg(command, 1, str)
            if on == None:
                print('repeaterfix is ' + 'on.' if self.repeaterfix else 'off.')
                return
            if not (on.strip().lower() in ['on', 'off']):
                print('please provide ON or OFF.')
                return
            self.repeaterfix = on.strip().lower() == 'on'
            print("changed the state of repeaterfix.")
        if command[0] == "tempomod":
            mod = self.try_get_arg(command, 1, float)
            if mod == None:
                print('the tempo modifier is ' + str(self.tempo_modifier))
                return
            self.tempo_modifier = mod
            print("changed the tempo modifier to " + str(self.tempo_modifier))
        if command[0] == "nbgen":
            x = self.try_get_arg(command, 1, int)
            y = self.try_get_arg(command, 2, int)
            z = self.try_get_arg(command, 3, int)
            direction = self.try_get_arg(command, 4, str)
            if not direction in ["north", "east", "south", "west"]:
                direction = None
            m = FilePathInputManager()
            print('please choose a path (if not prompted below, look for a file window)')
            midipath = m.get()
            if not os.path.isfile(midipath):
                print("invalid path")
                return
            
            x = self.input_if_none(x, "x position", int)
            y = self.input_if_none(y, "y position", int)
            z = self.input_if_none(z, "z position", int)

            while True:
                if direction in ['north', 'south', 'east', 'west']:
                    break
                print('input a direction (north/south/east/west)')
                direction = input('> ').strip().lower()
            direction = {'south': 0, 'east': 1, 'north': 2, 'west': 3}[direction]
            print('reading file..')
            c = NoteBlockConverter(midipath)
            c.tempo_modifier = self.tempo_modifier
            print('extracting file')
            c.extract_messages()
            print('generating notes')
            c.generate_noteblock_objects()
            g = NoteBlockStructureGenerator(c.noteblock)
            if (self.repeaterfix):
                g.facing = self.facing_repeaterfix
            print('generating structure')
            g.generate()
            print('starting minecraft server')
            print('building blocks..')
            self.minecraft_server.logging_disabled = True
            try:
                g.build(self.minecraft_server, x, y, z, direction)
            except BaseException as e:
                time.sleep(2)
                if not self.pythonw:
                    self.minecraft_server.logging_disabled = False
                raise e
            time.sleep(2)
            if not self.pythonw:
                self.minecraft_server.logging_disabled = False
            print('done!!')

    def run(self):
        self.ready_server()
        self.console()

a = NoteblockerCI()
a.run()
