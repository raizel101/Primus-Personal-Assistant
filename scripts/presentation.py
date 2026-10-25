"""Presentation controls backed by the real PRIMUS localhost API. No mock results."""
from pathlib import Path
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vendor'))
sys.path.insert(0,str(ROOT/'src'))
from primus.config import load_config
from primus.dashboard import api_request


def main():
    config=load_config(ROOT/'config.json')
    window=tk.Tk();window.title('PRIMUS Presentation')
    window.geometry('790x690+80+60');window.minsize(650,550)
    window.configure(bg='#101721')
    tk.Label(window,text='PRIMUS',font=('Segoe UI',26,'bold'),bg='#101721',fg='#64e9d2').pack(anchor='w',padx=22,pady=(18,0))
    tk.Label(window,text='Local agentic assistant   |   Real tools and real results',bg='#101721',fg='white',font=('Segoe UI',11)).pack(anchor='w',padx=22,pady=(2,10))
    tk.Label(window,text='Use buttons or type a request. Sensitive actions still require Approve once.\nWake-word recognition is optional and can miss phrases.',bg='#101721',fg='#b9cadb',justify='left').pack(anchor='w',padx=22)
    controls=tk.Frame(window,bg='#101721');controls.pack(fill='x',padx=20,pady=12)
    output=scrolledtext.ScrolledText(window,wrap='word',font=('Consolas',10),bg='#172230',fg='#e4eef9',insertbackground='white')
    entry=tk.Entry(window,font=('Segoe UI',12));entry.insert(0,'Open Google Chrome')
    results=queue.Queue();buttons=[];busy=False
    status=tk.StringVar(value='Checking the local service...')
    def show(text):
        output.configure(state='normal');output.delete('1.0','end');output.insert('end',text);output.configure(state='disabled')
    def start(label,task=None,path=None):
        nonlocal busy
        if busy:return
        busy=True;status.set(label+' — running; check for an approval dialog.');show('Working on: '+label)
        for button in buttons:button.configure(state='disabled')
        def work():
            try:
                result=api_request(config,path) if path else api_request(config,'/tasks',{'task':task},timeout=100)
                text=result.get('result') if isinstance(result,dict) and 'result' in result else json.dumps(result,indent=2)
                if task and task.startswith('planning:'):
                    plan=json.loads(text)
                    text='FORMAL STRIPS PLAN\nStatus: '+plan['status']+'\n\n'+'\n'.join(f'{i+1}. {name}' for i,name in enumerate(plan.get('plan') or []))+'\n\nReplay valid: '+str((plan.get('validation') or {}).get('valid'))+'\nModel only — these actions were not executed.\n\nFull result:\n'+json.dumps(plan,indent=2)
                results.put((True,text))
            except Exception as exc:
                results.put((False,'Request failed or was not approved. No success is claimed.\n\n'+str(exc)))
        threading.Thread(target=work,daemon=True).start()
    items=[('1  Service health',lambda:start('Service health',path='/health')),
           ('2  List output files',lambda:start('List output files',task='list my PRIMUS outputs folder')),
           ('3  List reminders',lambda:start('List reminders',task='what reminders are scheduled')),
           ('4  Formal planning',lambda:start('Formal planning',task='planning: examples/planning/downloads.json')),
           ('5  Open website in Chrome',lambda:start('Open example.com in Chrome',task='open https://example.com/ in Google Chrome')),
           ('6  Syllabus RAG with citations',lambda:start('Syllabus RAG — model response may take time',task='knowledge: What is the course code and how many credits does this course have?'))]
    for i,(label,command) in enumerate(items):
        b=tk.Button(controls,text=label,command=command,padx=8,pady=8,anchor='w');b.grid(row=i//2,column=i%2,sticky='ew',padx=3,pady=3);buttons.append(b)
    controls.columnconfigure(0,weight=1);controls.columnconfigure(1,weight=1)
    actions=tk.Frame(window,bg='#101721');actions.pack(fill='x',padx=22)
    def orb():
        (ROOT/'data/orb-open').write_text('Open for presentation until dismissed.\n',encoding='utf-8')
        status.set('Orb requested. It reflects voice state, not typed task progress.')
    def dashboard():
        subprocess.Popen([sys.executable,str(ROOT/'primus.py'),'dashboard'],cwd=ROOT)
        status.set('Mission Control requested; review its real files, checks and approvals.')
    tk.Button(actions,text='Show orb',command=orb).pack(side='left',padx=(0,8))
    tk.Button(actions,text='Open Mission Control',command=dashboard).pack(side='left')
    tk.Label(window,text='Or type a request',bg='#101721',fg='white').pack(anchor='w',padx=22,pady=(10,2))
    entry.pack(fill='x',padx=22)
    run=tk.Button(window,text='Run request',command=lambda:start('Typed request',task=entry.get().strip()));run.pack(anchor='e',padx=22,pady=5);buttons.append(run)
    tk.Label(window,textvariable=status,bg='#101721',fg='#64e9d2',anchor='w',wraplength=730).pack(fill='x',padx=22,pady=4)
    output.pack(fill='both',expand=True,padx=22,pady=(0,18))
    def poll():
        nonlocal busy
        try:
            ok,text=results.get_nowait();show(text);busy=False
            status.set('Result received from PRIMUS.' if ok else 'Request needs attention; see actual error below.')
            for b in buttons:b.configure(state='normal')
        except queue.Empty:pass
        window.after(100,poll)
    entry.bind('<Return>',lambda _:start('Typed request',task=entry.get().strip()))
    window.after(100,poll);window.after(250,lambda:start('Service health',path='/health'))
    window.lift();window.attributes('-topmost',True);window.after(1800,lambda:window.attributes('-topmost',False))
    window.mainloop()


if __name__=='__main__':main()
