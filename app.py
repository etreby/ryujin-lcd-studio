#!/usr/bin/env python3
"""Local graphical editor for the experimental Ryujin III Extreme LCD driver."""
import copy
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from backend import ApplyManager, DEFAULT_CONFIG_DIR, SERVICE_NAME, read_json, validate_display

HOME = Path.home()
CLI = HOME / '.local/bin/ryujin-lcd'
APP = Path(__file__).resolve()

def run_display(config_dir, cli):
    options = read_json(Path(config_dir) / 'display.json', default={'mode':'stats','path':''})
    mode = options.get('mode', 'stats')
    args = validate_display(options)
    if mode == 'stats':
        args = ['stats', str(Path(config_dir) / 'dashboard.json')]
    command = [str(cli), 'set', 'lcd', 'screen', *args]
    import os
    os.execv(command[0], command)

from PyQt5 import QtCore, QtGui, QtWidgets as W
from PIL import Image
from liquidctl.driver.asus_ryujin_lcd import (
    _default_lcd_stats_config, _render_lcd_stats, _validate_lcd_stats_config,
)
import sensors as host_sensors

STATS = {
    'liquid_temperature': 'Coolant temperature',
    'pump_speed': 'Pump speed', 'pump_duty': 'Pump duty',
    'pump_fan_speed': 'Pump fan speed', 'pump_fan_duty': 'Pump fan duty',
}
SAMPLE = [('Liquid temperature', 35.2, '°C'), ('Pump speed', 3060, 'rpm'),
          ('Pump duty', 40, '%'), ('Pump fan speed', 960, 'rpm'), ('Pump fan duty', 30, '%')]

def color(value):
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return '#' + ''.join(f'{int(channel):02X}' for channel in value)
    return '#' + str(value or 'B300FF').lstrip('#')

def profile_colors(config):
    """Convert driver default RGB tuples to JSON color strings for editing."""
    config = copy.deepcopy(config)
    for key in ('font_color', 'background_color'):
        value = config.get(key)
        if isinstance(value, (tuple, list)) and len(value) == 3:
            config[key] = color(value).lstrip('#')
    for card in config.get('layout', {}).values():
        for key in ('font_color', 'background_color'):
            value = card.get(key)
            if isinstance(value, (tuple, list)) and len(value) == 3:
                card[key] = color(value).lstrip('#')
    return config

class ColorButton(W.QPushButton):
    changed = QtCore.pyqtSignal(str)
    def __init__(self, value):
        super().__init__()
        self.setMinimumWidth(130)
        self.set_color(value)
        self.clicked.connect(self.choose)
    def set_color(self, value):
        self.value = color(value)
        c = QtGui.QColor(self.value)
        foreground = '#151518' if c.lightness() > 140 else '#FFFFFF'
        self.setText(self.value.upper())
        self.setStyleSheet(f'background:{self.value};color:{foreground};border:1px solid #73737d;padding:8px;border-radius:6px;')
        self.setAccessibleName('Choose color ' + self.value)
    def choose(self):
        c = W.QColorDialog.getColor(QtGui.QColor(self.value), self.window(), 'Choose LCD color')
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(self.value)

class ApplyWorker(QtCore.QThread):
    finished_result = QtCore.pyqtSignal(bool, str)
    def __init__(self, config, options, config_dir, dry_run, cli):
        super().__init__()
        self.config, self.options = config, options
        self.config_dir, self.dry_run, self.cli = config_dir, dry_run, cli
    def run(self):
        try:
            backup = ApplyManager(self.config_dir, dry_run=self.dry_run, cli=self.cli).apply(self.config, self.options)
            message = f'Validated in isolated preview mode ({self.config_dir}); no files or services changed.' if self.dry_run else f'Applied to your cooler. Backup: {backup}'
            self.finished_result.emit(True, message)
        except Exception as error:
            self.finished_result.emit(False, f'Could not apply: {error}')

class SensorDiscoveryWorker(QtCore.QThread):
    found = QtCore.pyqtSignal(object)
    def run(self):
        try:
            self.found.emit(host_sensors.discover())
        except Exception as error:
            self.found.emit(error)

class SensorSampleWorker(QtCore.QThread):
    sampled = QtCore.pyqtSignal(object)
    def __init__(self, sensor_ids, parent=None):
        super().__init__(parent); self.sensor_ids = tuple(sensor_ids)
    def run(self):
        try: self.sampled.emit(host_sensors.read_sensors(self.sensor_ids))
        except Exception: self.sampled.emit({sensor_id: None for sensor_id in self.sensor_ids})

class Window(W.QMainWindow):
    def __init__(self, config_dir=DEFAULT_CONFIG_DIR, dry_run=False, cli=CLI):
        super().__init__()
        self.config_dir, self.dry_run, self.cli = Path(config_dir), dry_run, Path(cli)
        try:
            self.config = read_json(self.config_dir / 'dashboard.json', default=profile_colors(_default_lcd_stats_config()))
            _validate_lcd_stats_config(copy.deepcopy(self.config))
            self.options = read_json(self.config_dir / 'display.json', default={'mode':'stats','path':''})
        except Exception as exc:
            raise ValueError(f'Could not load settings from {self.config_dir}: {exc}') from exc
        self.setWindowTitle('Ryujin LCD Studio')
        self.resize(1080, 760)
        self.setMinimumSize(900, 620)
        self.worker = None
        self.sensor_worker = None
        self.sample_worker = None
        self.sensor_values = {}
        self.sensor_timestamps = {}
        self.live_enabled = not dry_run
        self.sensor_close_pending = False
        self.sensor_items = []
        self.loading = True
        self.dirty = False
        self.movie = None
        root = W.QWidget(); self.setCentralWidget(root)
        outer = W.QVBoxLayout(root); outer.setContentsMargins(26,22,26,18); outer.setSpacing(18)
        heading = W.QHBoxLayout()
        titles = W.QVBoxLayout()
        title = W.QLabel('Ryujin LCD Studio'); title.setObjectName('title')
        titles.addWidget(title); titles.addWidget(W.QLabel('Make the cooler display yours.'))
        heading.addLayout(titles); heading.addStretch()
        self.service_label = W.QLabel('Checking display…'); heading.addWidget(self.service_label)
        outer.addLayout(heading)
        split = W.QHBoxLayout(); split.setSpacing(26); outer.addLayout(split, 1)
        controls = W.QWidget(); controls.setMinimumWidth(350); controls.setMaximumWidth(430)
        left = W.QVBoxLayout(controls); left.setContentsMargins(0,0,0,0)
        scroll = W.QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(W.QFrame.NoFrame)
        # Leave room for the vertical scrollbar so the scrollable form's
        # minimum width never triggers a horizontal bar at the 900 px window.
        scroll.setMinimumWidth(364)
        scroll.setWidget(controls); split.addWidget(scroll, 1)
        form = W.QFormLayout(); form.setVerticalSpacing(12); left.addLayout(form)
        self.mode = W.QComboBox()
        for text, data in [('Live dashboard','stats'),('Static picture','static'),('Animated GIF','gif')]: self.mode.addItem(text,data)
        self.mode.setCurrentIndex(max(0,self.mode.findData(self.options.get('mode','stats'))))
        form.addRow('Display',self.mode)
        self.media_widget = W.QWidget(); media = W.QVBoxLayout(self.media_widget); media.setContentsMargins(0,0,0,0)
        self.file_label = W.QLabel(self.options.get('path') or 'No file selected'); self.file_label.setWordWrap(True)
        choose = W.QPushButton('Choose picture or GIF…'); choose.clicked.connect(self.choose_media)
        media.addWidget(self.file_label); media.addWidget(choose); left.addWidget(self.media_widget)
        self.dashboard_widget = W.QWidget(); dash = W.QVBoxLayout(self.dashboard_widget); dash.setContentsMargins(0,0,0,0); dash.setSpacing(14)
        left.addWidget(self.dashboard_widget)
        general = W.QFormLayout(); general.setVerticalSpacing(12); dash.addLayout(general)
        self.title_edit = W.QLineEdit(self.config.get('title','RYUJIN III EXTREME')); self.title_edit.setMaxLength(80)
        general.addRow('Screen title',self.title_edit)
        self.font_color = ColorButton(self.config.get('font_color')); general.addRow('All text',self.font_color)
        self.bg_color = ColorButton(self.config.get('background_color','100713')); general.addRow('Screen background',self.bg_color)
        self.interval = W.QSpinBox(); self.interval.setRange(1,60); self.interval.setSuffix(' seconds'); self.interval.setValue(round(self.config.get('interval',5)))
        general.addRow('Rotate values every',self.interval)
        self.cards = W.QComboBox(); dash.addWidget(W.QLabel('Dashboard cards')); dash.addWidget(self.cards)
        row = W.QHBoxLayout(); add=W.QPushButton('Add card'); remove=W.QPushButton('Remove card'); row.addWidget(add); row.addWidget(remove); dash.addLayout(row)
        add.clicked.connect(self.add_card); remove.clicked.connect(self.remove_card)
        self.card_form = W.QFormLayout(); self.card_form.setVerticalSpacing(12); dash.addLayout(self.card_form)
        self.card_text=ColorButton('B300FF'); self.card_bg=ColorButton('210B2D')
        self.card_form.addRow('Card text',self.card_text); self.card_form.addRow('Card background',self.card_bg)
        self.stat_rows = W.QVBoxLayout(); dash.addLayout(self.stat_rows)
        sensor_row=W.QHBoxLayout(); self.sensor_status=W.QLabel('Discovering host sensors…'); sensor_row.addWidget(self.sensor_status,1)
        self.refresh_sensors_btn=W.QPushButton('Refresh sensors'); self.refresh_sensors_btn.clicked.connect(self.refresh_sensors); sensor_row.addWidget(self.refresh_sensors_btn); dash.addLayout(sensor_row)
        add_value=W.QPushButton('Add rotating value'); add_value.clicked.connect(self.add_value); dash.addWidget(add_value)
        self.live_preview=W.QCheckBox('Live host readings in preview')
        self.live_preview.setChecked(self.live_enabled)
        self.live_preview.setToolTip('Reads only selected built-in host sensor IDs. Saved custom commands are never run.')
        self.live_preview.toggled.connect(self.live_preview_changed); dash.addWidget(self.live_preview)
        hint=W.QLabel('Each card can rotate through several readings. Cooler values remain examples; selected host sensors can update live.')
        hint.setWordWrap(True); hint.setObjectName('hint'); dash.addWidget(hint); left.addStretch()
        right=W.QVBoxLayout(); split.addLayout(right, 2)
        label=W.QLabel('LCD preview'); label.setObjectName('section'); right.addWidget(label)
        self.preview=W.QLabel(); self.preview.setAlignment(QtCore.Qt.AlignCenter); self.preview.setMinimumSize(240,180); self.preview.setSizePolicy(W.QSizePolicy.Ignored,W.QSizePolicy.Ignored); self.preview.setObjectName('preview')
        right.addWidget(self.preview,1)
        self.preview_note=W.QLabel('640 × 480 · Example readings'); self.preview_note.setAlignment(QtCore.Qt.AlignCenter); right.addWidget(self.preview_note)
        pages=W.QHBoxLayout(); pages.addStretch(); self.next_page=W.QPushButton('Preview next values'); self.next_page.clicked.connect(self.advance); pages.addWidget(self.next_page); pages.addStretch(); right.addLayout(pages)
        self.page=0
        self.message=W.QLabel('Changes appear on the cooler only after Apply.'); self.message.setWordWrap(True); outer.addWidget(self.message)
        footer=W.QHBoxLayout(); log=W.QPushButton('View display log'); log.clicked.connect(self.show_log); footer.addWidget(log)
        self.revert_btn=W.QPushButton('Revert changes'); self.revert_btn.clicked.connect(self.revert); footer.addWidget(self.revert_btn)
        enabled = (not self.dry_run and subprocess.run(['systemctl','--user','is-enabled','--quiet',SERVICE_NAME],capture_output=True).returncode==0)
        self.login=W.QCheckBox('Start display at login'); self.login.setChecked(enabled); footer.addWidget(self.login); self.login.toggled.connect(self.autostart)
        footer.addStretch(); self.apply_btn=W.QPushButton('Apply to cooler'); self.apply_btn.setObjectName('apply'); self.apply_btn.clicked.connect(self.apply); footer.addWidget(self.apply_btn); outer.addLayout(footer)
        self.title_edit.textChanged.connect(self.general_changed); self.interval.valueChanged.connect(self.general_changed)
        self.font_color.changed.connect(self.all_text); self.bg_color.changed.connect(self.general_changed)
        self.card_text.changed.connect(self.card_colors); self.card_bg.changed.connect(self.card_colors)
        self.cards.currentIndexChanged.connect(self.load_card); self.mode.currentIndexChanged.connect(self.mode_changed)
        self.populate_cards(); self.loading=False; self.mode_changed(mark=False); self.refresh_sensors()
        self.apply_btn.setEnabled(False); self.revert_btn.setEnabled(False)
        self.timer=QtCore.QTimer(self); self.timer.timeout.connect(self.refresh_status); self.timer.start(5000); self.refresh_status()
        self.sample_timer=QtCore.QTimer(self); self.sample_timer.timeout.connect(self.sample_selected); self.sample_timer.start(2000)
        self.sample_debounce=QtCore.QTimer(self); self.sample_debounce.setSingleShot(True); self.sample_debounce.setInterval(100); self.sample_debounce.timeout.connect(self.sample_selected)
        self.schedule_sample()
        W.QShortcut(QtGui.QKeySequence.Save, self, activated=self.apply)
    def mark(self):
        if not self.loading:
            self.dirty=True; self.message.setText('Unsaved changes · Apply to update the cooler.'); self.apply_btn.setEnabled(True); self.revert_btn.setEnabled(True); self.render()
    def general_changed(self,*args):
        if self.loading:return
        self.config.update(title=self.title_edit.text(),interval=self.interval.value(),font_color=self.font_color.value.lstrip('#'),background_color=self.bg_color.value.lstrip('#'))
        self.mark()
    def all_text(self,*args):
        for card in self.config['layout'].values():card['font_color']=self.font_color.value.lstrip('#')
        self.general_changed(); self.load_card()
    def current_card(self):return self.config['layout'].get(self.cards.currentText())
    def card_colors(self,*args):
        c=self.current_card()
        if c is not None:
            c.update(font_color=self.card_text.value.lstrip('#'),background_color=self.card_bg.value.lstrip('#')); self.mark()
    def populate_cards(self):
        self.cards.blockSignals(True); self.cards.clear(); self.cards.addItems(list(self.config['layout'])); self.cards.blockSignals(False); self.load_card()
    def load_card(self,*args):
        c=self.current_card()
        if c is None:return
        self.stat_preview_labels=[]
        self.card_text.set_color(c.get('font_color') or self.config['font_color']); self.card_bg.set_color(c.get('background_color') or self.config['background_color'])
        while self.stat_rows.count():
            item=self.stat_rows.takeAt(0)
            if item.widget():item.widget().deleteLater()
        for i,stat in enumerate(c['stats']):
            widget=W.QWidget(); row=W.QVBoxLayout(widget); row.setContentsMargins(0,4,0,4)
            label=W.QLineEdit(stat['indicator']); label.setPlaceholderText('LCD label'); label.setMaxLength(40); label.setAccessibleName('Reading label')
            line=W.QHBoxLayout(); combo=W.QComboBox()
            combo.setEditable(True); combo.setInsertPolicy(W.QComboBox.NoInsert)
            combo.setCompleter(W.QCompleter(combo.model(), combo))
            combo.completer().setFilterMode(QtCore.Qt.MatchContains)
            combo.completer().setCompletionMode(W.QCompleter.PopupCompletion)
            combo.setSizeAdjustPolicy(W.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(18)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Fixed)
            for key,text in STATS.items():combo.addItem(text,key)
            grouped={}
            for sensor in self.sensor_items: grouped.setdefault(self.sensor_group(sensor),[]).append(sensor)
            gpu_groups=sorted((group for group in grouped if group.startswith('GPU ')),key=lambda value:int(value.split()[1]))
            group_order=['CPU','Memory & swap',*gpu_groups,'Cooler & motherboard','SSD / DIMM','Other hwmon']
            for group in group_order:
                sensors=grouped.get(group,[])
                if not sensors:continue
                combo.addItem('— '+group+' —',None)
                header=combo.model().item(combo.count()-1)
                if header:header.setFlags(QtCore.Qt.NoItemFlags)
                for sensor in sensors:
                    combo.addItem(f"{sensor['label']} · {sensor['unit']}", 'sensor:'+sensor['id'])
                    combo.setItemData(combo.count()-1, sensor.get('description', sensor['label']), QtCore.Qt.ToolTipRole)
            sensor_id=self.recognized_sensor(stat)
            if sensor_id:
                if not any(x['id']==sensor_id for x in self.sensor_items):
                    combo.addItem(f'Unavailable host sensor · {sensor_id}', 'sensor:'+sensor_id)
                combo.setCurrentIndex(combo.findData('sensor:'+sensor_id))
            else:
                if stat['stat']=='custom':combo.addItem('Existing custom reading','custom')
                combo.setCurrentIndex(combo.findData(stat['stat']))
            remove=W.QPushButton('Remove'); line.addWidget(combo,1); line.addWidget(remove); row.addLayout(line); row.addWidget(label)
            widget.setMinimumWidth(0); widget.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Preferred)
            label.textChanged.connect(lambda text,s=stat:self.edit_stat(s,'indicator',text))
            combo.currentIndexChanged.connect(lambda _,s=stat,b=combo:self.change_stat(s,b.currentData()))
            live=W.QLabel(self.sensor_preview_text(stat)); live.setObjectName('hint'); live.setWordWrap(True)
            self.stat_preview_labels.append((live,stat))
            remove.clicked.connect(lambda _,idx=i:self.remove_value(idx))
            self.stat_rows.addWidget(widget); self.stat_rows.addWidget(live)
        self.schedule_sample()
    def sensor_group(self,sensor):
        sid=sensor['id']; label=sensor.get('label','').lower()
        if sid.startswith('cpu.') or (sid.startswith('hwmon.') and any(word in label for word in ('cpu','coretemp'))):return 'CPU'
        if sid.startswith(('memory.','swap.')):return 'Memory & swap'
        if sid.startswith('gpu.'):
            try:return 'GPU '+str(1+sorted({x.get('uuid') for x in self.sensor_items if x['id'].startswith('gpu.')}).index(sensor.get('uuid')))
            except ValueError:return 'GPU 1'
        if any(word in label for word in ('ssd','nvme')):return 'SSD / DIMM'
        if any(word in label for word in ('dimm','spd')):return 'SSD / DIMM'
        if sid.startswith('hwmon.') and any(word in label for word in ('asus','liquid','pump','fan')):return 'Cooler & motherboard'
        return 'Other hwmon'
    def sensor_preview_text(self,stat):
        sensor_id=self.recognized_sensor(stat)
        if not sensor_id:return 'Example reading · existing custom command is kept but never run here.' if stat.get('stat')=='custom' else 'Cooler example reading'
        if not self.live_enabled:return 'Live preview is off · example value'
        value=self.sensor_values.get(sensor_id)
        if value is None:return 'Live host reading · N/A / waiting for sample'
        if time.monotonic()-self.sensor_timestamps.get(sensor_id,0)>6:return 'Live host reading · stale; refreshing'
        sensor=next((x for x in self.sensor_items if x['id']==sensor_id),{})
        return f"Live host reading · {value:g} {sensor.get('unit',stat.get('unit',''))}"
    def refresh_sensor_labels(self):
        for label,stat in getattr(self,'stat_preview_labels',[]):
            label.setText(self.sensor_preview_text(stat))
        self.render()
    def live_preview_changed(self,enabled):
        self.live_enabled=enabled; self.schedule_sample(); self.refresh_sensor_labels()
    def selected_sensor_ids(self):
        ids=[]
        if self.options.get('mode')=='stats':
            for card in self.config.get('layout',{}).values():
                for stat in card.get('stats',[]):
                    sensor_id=self.recognized_sensor(stat)
                    if sensor_id and any(x['id']==sensor_id for x in self.sensor_items):ids.append(sensor_id)
        return list(dict.fromkeys(ids))
    def schedule_sample(self):
        if hasattr(self,'sample_debounce'):self.sample_debounce.start()
    def sample_selected(self):
        if self.sensor_close_pending or not self.live_enabled or not self.isVisible() or self.options.get('mode')!='stats':return
        if self.sample_worker and self.sample_worker.isRunning():return
        ids=self.selected_sensor_ids()
        if not ids:return
        self.sample_worker=SensorSampleWorker(ids,self); self.sample_worker.sampled.connect(self.sensor_sampled); self.sample_worker.start()
    def sensor_sampled(self,values):
        now=time.monotonic()
        selected=set(self.selected_sensor_ids())
        for sensor_id,value in values.items():
            if sensor_id in selected:
                self.sensor_values[sensor_id]=value
                self.sensor_timestamps[sensor_id]=now
        self.refresh_sensor_labels()
    def edit_stat(self,stat,key,value):stat[key]=value; self.mark()
    def recognized_sensor(self,stat):
        command=stat.get('command',[])
        if len(command)==4 and command[0]==str(host_sensors.PYTHON) and command[1]==str(host_sensors.APP_DIR/'sensors.py') and command[2]=='--read':
            return command[3]
        return None
    def refresh_sensors(self):
        if self.sensor_close_pending or (self.sensor_worker and self.sensor_worker.isRunning()):return
        self.refresh_sensors_btn.setEnabled(False); self.sensor_status.setText('Discovering host sensors…')
        self.sensor_worker=SensorDiscoveryWorker(self); self.sensor_worker.found.connect(self.sensors_refreshed); self.sensor_worker.start()
    def sensors_refreshed(self,result):
        self.refresh_sensors_btn.setEnabled(True)
        if isinstance(result,Exception):self.sensor_status.setText('Sensor discovery unavailable');return
        self.sensor_items=result; self.sensor_status.setText(f'{len(result)} host readings discovered · read-only')
        present={item['id'] for item in result}
        self.sensor_values={sensor_id:value for sensor_id,value in self.sensor_values.items() if sensor_id in present}
        self.sensor_timestamps={sensor_id:value for sensor_id,value in self.sensor_timestamps.items() if sensor_id in present}
        self.load_card()
    def change_stat(self,stat,value):
        if value in ('custom',None):return
        label=stat['indicator']
        if isinstance(value,str) and value.startswith('sensor:'):
            sensor=next((x for x in self.sensor_items if x['id']==value[7:]),None)
            if sensor:
                if label.strip().upper() in ('COOLANT','LIQUID TEMP','VALUE'):
                    label=sensor['label'].split('·')[0].strip().upper()[:40]
                stat.clear(); stat.update(stat='custom',indicator=label,command=[str(host_sensors.PYTHON),str(host_sensors.APP_DIR/'sensors.py'),'--read',sensor['id']],unit=sensor['unit'])
        else:
            stat.clear(); stat.update(stat=value,indicator=label)
        self.mark(); self.refresh_sensor_labels(); self.schedule_sample()
    def add_value(self):
        c=self.current_card()
        if len(c['stats'])>=10:self.message.setText('Use up to ten rotating values per card.'); return
        c['stats'].append({'stat':'liquid_temperature','indicator':'COOLANT'}); self.load_card(); self.mark()
    def remove_value(self,index):
        c=self.current_card()
        if len(c['stats'])==1:self.message.setText('Keep at least one reading in each card.'); return
        c['stats'].pop(index); self.load_card(); self.mark()
    def add_card(self):
        layout=self.config['layout']
        if len(layout)>=4:self.message.setText('The screen supports up to four cards.');return
        i=1
        while f'card_{i}' in layout:i+=1
        layout[f'card_{i}']={'font_color':self.config['font_color'],'background_color':'210B2D','stats':[{'stat':'liquid_temperature','indicator':'COOLANT'}]}
        self.populate_cards(); self.cards.setCurrentIndex(self.cards.count()-1); self.mark()
    def remove_card(self):
        if len(self.config['layout'])==1:self.message.setText('Keep at least one dashboard card.');return
        del self.config['layout'][self.cards.currentText()]; self.populate_cards(); self.mark()
    def choose_media(self):
        path,_=W.QFileDialog.getOpenFileName(self,'Choose LCD media',str(HOME/'Pictures'),'Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)')
        if path:self.options['path']=path; self.file_label.setText(path); self.mark()
    def mode_changed(self,*args,mark=True):
        self.options['mode']=self.mode.currentData(); stats=self.options['mode']=='stats'
        self.dashboard_widget.setVisible(stats); self.media_widget.setVisible(not stats); self.next_page.setVisible(stats)
        if mark:self.mark()
        self.render()
    def advance(self):self.page+=1; self.render()
    def render(self):
        try:
            if self.movie:self.movie.stop();self.movie=None
            if self.options['mode']=='stats':
                cfg=copy.deepcopy(self.config)
                # Preview custom readings without executing commands.
                custom={}
                for i,c in enumerate(cfg['layout'].values()):
                    stat=c['stats'][self.page%len(c['stats'])]
                    if stat['stat']!='custom':continue
                    sensor_id=self.recognized_sensor(stat)
                    value=self.sensor_values.get(sensor_id) if self.live_enabled and sensor_id else None
                    fresh=value is not None and time.monotonic()-self.sensor_timestamps.get(sensor_id,0)<=6
                    custom[i]=(f'{value:g}' if fresh else 'N/A',stat.get('unit','')) if sensor_id and self.live_enabled else ('Example',stat.get('unit',''))
                render_cfg=_validate_lcd_stats_config(copy.deepcopy(cfg))
                raw=_render_lcd_stats(SAMPLE,render_cfg,self.page,render_cfg['interval'],custom_values=custom)
                img=QtGui.QImage(raw,640,480,640*3,QtGui.QImage.Format_RGB888).copy()
                self.pixmap=QtGui.QPixmap.fromImage(img)
                has_host=any(self.recognized_sensor(s) for c in cfg['layout'].values() for s in c['stats'])
                live_fresh=any(self.recognized_sensor(s) and self.sensor_values.get(self.recognized_sensor(s)) is not None and time.monotonic()-self.sensor_timestamps.get(self.recognized_sensor(s),0)<=6 for c in cfg['layout'].values() for s in c['stats'])
                note='Live host readings + cooler examples' if live_fresh else 'Live host readings unavailable · cooler examples'
                self.preview_note.setText(f'640 × 480 · {note}' if self.live_enabled and has_host else '640 × 480 · Example readings')
            else:
                path=self.options.get('path','')
                if not path:self.preview.clear();self.preview.setText('Choose a picture to preview it here.');return
                with Image.open(path) as image:
                    frame=image.convert('RGB').resize((640,480)); raw=frame.tobytes()
                self.pixmap=QtGui.QPixmap.fromImage(QtGui.QImage(raw,640,480,1920,QtGui.QImage.Format_RGB888).copy())
                self.preview_note.setText('640 × 480 · Image resized to fit the LCD')
                if self.options['mode']=='gif':
                    self.movie=QtGui.QMovie(path); self.movie.setScaledSize(QtCore.QSize(640,480)); self.movie.frameChanged.connect(self.movie_frame); self.movie.start()
            self.fit_preview()
        except Exception as e:self.message.setText(f'Preview unavailable: {e}')
    def movie_frame(self,*args):
        if self.movie:self.pixmap=self.movie.currentPixmap();self.fit_preview()
    def fit_preview(self):
        if hasattr(self,'pixmap'):
            area=self.preview.contentsRect().size()
            self.preview.setPixmap(self.pixmap.scaled(area,QtCore.Qt.KeepAspectRatio,QtCore.Qt.SmoothTransformation))
    def resizeEvent(self,event):super().resizeEvent(event);self.fit_preview()
    def apply(self):
        if not self.dirty:return
        self.apply_btn.setEnabled(False); self.centralWidget().setEnabled(False); self.message.setText('Applying display settings…')
        self.worker=ApplyWorker(copy.deepcopy(self.config),copy.deepcopy(self.options),self.config_dir,self.dry_run,self.cli); self.worker.finished_result.connect(self.applied); self.worker.start()
    def applied(self,ok,message):
        self.centralWidget().setEnabled(True); self.message.setText(message)
        if ok:
            self.dirty=False; self.apply_btn.setEnabled(False); self.revert_btn.setEnabled(False)
        else:self.apply_btn.setEnabled(self.dirty)
        self.refresh_status()
    def revert(self):
        if not self.dirty:return
        answer=W.QMessageBox.question(self,'Revert changes?','Discard editor changes and reload the saved profile?',W.QMessageBox.Discard|W.QMessageBox.Cancel)
        if answer!=W.QMessageBox.Discard:return
        try:
            config=read_json(self.config_dir/'dashboard.json',default=profile_colors(_default_lcd_stats_config()))
            _validate_lcd_stats_config(copy.deepcopy(config))
            options=read_json(self.config_dir/'display.json',default={'mode':'stats','path':''})
        except Exception as exc:
            self.message.setText(f'Could not reload saved profile: {exc}'); return
        self.loading=True; self.config=config; self.options=options
        self.title_edit.setText(self.config.get('title','RYUJIN III EXTREME'))
        self.font_color.set_color(self.config.get('font_color')); self.bg_color.set_color(self.config.get('background_color','100713'))
        self.interval.setValue(round(self.config.get('interval',5)))
        self.file_label.setText(self.options.get('path') or 'No file selected')
        self.mode.setCurrentIndex(max(0,self.mode.findData(self.options.get('mode','stats'))))
        self.populate_cards(); self.loading=False; self.mode_changed(mark=False)
        self.dirty=False; self.apply_btn.setEnabled(False); self.revert_btn.setEnabled(False)
        self.message.setText('Changes reverted · saved profile reloaded.'); self.schedule_sample()
    def refresh_status(self):
        if self.dry_run:
            self.service_label.setText('Isolated preview · no service access'); return
        try:
            p=subprocess.run(['systemctl','--user','is-active',SERVICE_NAME],capture_output=True,text=True,timeout=3)
            self.service_label.setText('Display running' if p.stdout.strip()=='active' else 'Display stopped')
        except (OSError, subprocess.SubprocessError): self.service_label.setText('Display status unavailable')
    def autostart(self,on):
        if self.dry_run:
            self.login.blockSignals(True); self.login.setChecked(False); self.login.blockSignals(False)
            self.message.setText('Login startup is unavailable in isolated preview mode.'); return
        p=subprocess.run(['systemctl','--user','enable' if on else 'disable',SERVICE_NAME],capture_output=True,text=True)
        if p.returncode:self.message.setText('Could not change startup: '+p.stderr)
    def show_log(self):
        log='No live service access in isolated preview mode.' if self.dry_run else subprocess.run(['journalctl','--user','-u',SERVICE_NAME,'-n','40','--no-pager'],capture_output=True,text=True).stdout
        dlg=W.QDialog(self);dlg.setWindowTitle('Cooler display log');dlg.resize(760,460);layout=W.QVBoxLayout(dlg);text=W.QPlainTextEdit(log);text.setReadOnly(True);layout.addWidget(text);dlg.exec_()
    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():event.ignore();return
        active=[worker for worker in (self.sensor_worker,self.sample_worker) if worker and worker.isRunning()]
        if active:
            event.ignore()
            if not self.sensor_close_pending:
                self.sensor_close_pending=True
                self.sample_timer.stop(); self.sample_debounce.stop()
                for worker in active:worker.finished.connect(self._sensor_worker_finished)
            return
        if self.dirty and W.QMessageBox.question(self,'Discard changes?','Close without applying your changes?',W.QMessageBox.Discard|W.QMessageBox.Cancel)!=W.QMessageBox.Discard:
            event.ignore(); self.sensor_close_pending=False
            if self.live_enabled:self.sample_timer.start(2000); self.sample_debounce.start(100)
            return
        event.accept()
    def _sensor_worker_finished(self):
        if not any(worker and worker.isRunning() for worker in (self.sensor_worker,self.sample_worker)):
            self.sensor_close_pending=False; self.close()

def main():
    parser = argparse.ArgumentParser(description='Customize the ASUS Ryujin III Extreme LCD.')
    parser.add_argument('--config-dir', type=Path, help='isolated profile directory; disables live service access')
    parser.add_argument('--dry-run', action='store_true', help='validate and preview without writing or using systemd')
    parser.add_argument('--run-display', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--cli', type=Path, default=CLI, help=argparse.SUPPRESS)
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--screenshot', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    config_dir = args.config_dir or DEFAULT_CONFIG_DIR
    if args.run_display:
        if args.dry_run:
            parser.error('--dry-run cannot be combined with --run-display')
        run_display(config_dir, args.cli)
        return
    app=W.QApplication(sys.argv);app.setApplicationName('Ryujin LCD Studio');app.setDesktopFileName('ryujin-lcd-studio');app.setStyle('Fusion')
    app.setStyleSheet('''QWidget { background:#202025; color:#eeeeF2; font-size:13px; }
    QLabel#title { font-size:26px; font-weight:600; } QLabel#section { font-size:17px; font-weight:600; }
    QLabel#hint { color:#b9b9c6; font-size:12px; } QLabel#preview { background:#111114; border-radius:12px; }
    QLineEdit,QComboBox,QSpinBox { background:#303037; border:1px solid #5b5b66; border-radius:6px; padding:7px; }
    QPushButton { background:#35353e; border:1px solid #60606c; border-radius:6px; padding:8px 12px; }
    QPushButton:hover { background:#494955; } QPushButton:focus,QLineEdit:focus,QComboBox:focus { border:2px solid #bd90ff; }
    QPushButton#apply { background:#8251bb; color:white; font-weight:600; padding:10px 20px; }
    QPushButton#apply:disabled { background:#303036; color:#93939e; border-color:#4d4d56; }
    QPushButton:disabled { color:#93939e; background:#303036; } QScrollArea { border:0; }
    QToolTip { background:#35353e; color:white; border:1px solid #60606c; }
    ''')
    try:
        win=Window(config_dir=config_dir,dry_run=(args.dry_run or args.config_dir is not None),cli=args.cli)
    except Exception as exc:
        W.QMessageBox.critical(None,'Could not load cooler settings',str(exc))
        return 2
    win.show()
    if args.smoke_test:
        def check():
            try:
                assert win.isVisible() and not win.pixmap.isNull()
                win.font_color.set_color('00FFFF');win.all_text()
                assert all(c['font_color']=='00FFFF' for c in win.config['layout'].values())
                win.advance();win.add_card();win.remove_card()
                assert not win.pixmap.isNull()
                if args.screenshot:
                    if not win.grab().save(str(args.screenshot)):
                        raise RuntimeError(f'Could not save screenshot to {args.screenshot}')
                win.dirty=False;print('PASS: window, real-renderer preview, color propagation, page rotation and card editing',flush=True);app.exit(0)
            except Exception:
                import traceback;traceback.print_exc();app.exit(1)
        QtCore.QTimer.singleShot(800,check)
    return app.exec_()
if __name__=='__main__':sys.exit(main())
