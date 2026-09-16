#include <winsock2.h>
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#define PORT 47611
static void w2u(const wchar_t *w, char *d, int cap){ if(!w){d[0]=0;return;} int n=WideCharToMultiByte(CP_UTF8,0,w,-1,d,cap,NULL,NULL); if(n<=0)d[0]=0; }
static int esc_json(const char *s, char *d, int cap){
  int n=0;
  for(const unsigned char *p=(const unsigned char*)s; *p && n<cap-7; p++){
    unsigned char c=*p;
    if(c=='\\'||c=='"'){ d[n++]='\\'; d[n++]=(char)c; }
    else if(c=='\n'){ d[n++]='\\'; d[n++]='n'; }
    else if(c=='\r'){ d[n++]='\\'; d[n++]='r'; }
    else if(c=='\t'){ d[n++]='\\'; d[n++]='t'; }
    else if(c<0x20){ n+=_snprintf(d+n,cap-n,"\\u%04x",c); }
    else d[n++]=(char)c;
  }
  d[n]=0; return n;
}
static int try_connect(void){
  SOCKET s=socket(AF_INET,SOCK_STREAM,0);
  struct sockaddr_in a; memset(&a,0,sizeof a);
  a.sin_family=AF_INET; a.sin_port=htons(PORT); a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
  if(connect(s,(struct sockaddr*)&a,sizeof a)<0){ closesocket(s); return -1; }
  return (int)s;
}
static size_t qwl(wchar_t *d, int cap, const wchar_t *s){
  size_t n=0,k=0; if(cap<3)return 0; d[n++]='"';
  for(const wchar_t *p=s;*p && n<(size_t)cap-8;p++){
    if(*p=='\\'){k++;continue;}
    if(*p=='"'){for(size_t j=0;j<2*k+1 && n<(size_t)cap-3;j++)d[n++]='\\';d[n++]='"';k=0;continue;}
    for(size_t j=0;j<k && n<(size_t)cap-3;j++)d[n++]='\\'; k=0; d[n++]=*p;
  }
  for(size_t j=0;j<2*k && n<(size_t)cap-3;j++)d[n++]='\\';
  d[n++]='"'; d[n]=0; return n;
}
static void spawn_daemon(void){
  wchar_t exe[MAX_PATH], cmd[32768], script[MAX_PATH];
  const wchar_t *e=_wgetenv(L"USERPROFILE"); if(!e)e=L"C:\\Users\\probs";
  const wchar_t *pyenv=_wgetenv(L"SEARCHWRAP_PYTHON");
  if(pyenv && GetFileAttributesW(pyenv)!=INVALID_FILE_ATTRIBUTES){
    wcsncpy(exe,pyenv,MAX_PATH-1); exe[MAX_PATH-1]=0;
  } else {
    _snwprintf(exe,MAX_PATH,L"%s\\.search-daemon\\Scripts\\python.exe",e);
    if(GetFileAttributesW(exe)==INVALID_FILE_ATTRIBUTES){
      const wchar_t *v=_wgetenv(L"VIRTUAL_ENV");
      if(v){_snwprintf(exe,MAX_PATH,L"%s\\Scripts\\python.exe",v);}
      else wcscpy(exe,L"python.exe");
    }
  }
  _snwprintf(script,MAX_PATH,L"%s\\bin\\search.py",e);
  size_t n=qwl(cmd,32768,exe); cmd[n++]=L' '; n+=qwl(cmd+n,(int)(32768-n),script);
  _snwprintf(cmd+n,(int)(32768-n),L" serve");
  STARTUPINFOW si; PROCESS_INFORMATION pi; memset(&si,0,sizeof si); memset(&pi,0,sizeof pi); si.cb=sizeof si;
  CreateProcessW(NULL,cmd,NULL,NULL,FALSE,0x08000000,NULL,NULL,&si,&pi);
}
static const char *findkey(const char *j, const char *k){
  char pat[64]; _snprintf(pat,sizeof pat,"\"%s\":",k);
  const char *p=strstr(j,pat); if(!p)return NULL;
  p+=strlen(pat);
  while(*p==' '||*p=='\t')p++;
  return p;
}
static int jstr(const char *j, const char *k, char *out, int cap){
  const char *p=findkey(j,k); if(!p||*p!='"'){out[0]=0;return 0;}
  p++; int n=0;
  while(*p && *p!='"' && n<cap-1){ if(*p=='\\'&&p[1])p++; out[n++]=*p++; }
  out[n]=0; return 1;
}
static long jnum(const char *j, const char *k){
  const char *p=findkey(j,k); if(!p)return -1;
  return strtol(p,NULL,10);
}
static void print_human(const char *r){
  char lane[32],win[32];
  jstr(r,"lane",lane,32); jstr(r,"winner",win,32);
  long ms=jnum(r,"ms"), tot=jnum(r,"total");
  int hits=0; const char *p=r; const char *det=strstr(r,"\"engines_detail\"");
  size_t span=det?(size_t)(det-r):strlen(r);
  while((p=strstr(p,"\"path\""))&&(size_t)(p-r)<span){hits++;p+=6;}
  if(!lane[0]){ fputs(r,stdout); return; }
  printf("lane=%s winner=%s ms=%ld hits=%d total=%ld\n",lane,win[0]?win:"none",ms,hits,tot);
  p=r; const char *end=det?det:r+strlen(r);
  while((p=strstr(p,"\"path\"")) && p<end){
    if(p>=end)break;
    p+=6;
    while(*p==':'||*p==' ')p++;
    if(*p!='"')continue;
    p++; char path[1024]; int n=0;
    while(*p && *p!='"' && n<1023){ if(*p=='\\'&&p[1])p++; path[n++]=*p++; }
    path[n]=0;
    printf("    %s\n",path);
  }
}
static void run_raw(int argc, wchar_t **argv, int rawi, const char *type, const wchar_t *treew){
  wchar_t eng[MAX_PATH];
  if(type[0] && !strcmp(type,"content")){
    const wchar_t *rgp=_wgetenv(L"RG_EXE");
    if(rgp && GetFileAttributesW(rgp)!=INVALID_FILE_ATTRIBUTES) wcscpy(eng,rgp);
    else if(!SearchPathW(NULL,L"rg.exe",NULL,MAX_PATH,eng,NULL)) wcscpy(eng,L"C:\\Users\\probs\\scoop\\shims\\rg.exe");
  } else {
    const wchar_t *e=_wgetenv(L"USERPROFILE"); if(!e)e=L"C:\\Users\\probs";
    _snwprintf(eng,MAX_PATH,L"%s\\bin\\es.exe",e);
  }
  wchar_t cmd[32768]; size_t n=qwl(cmd,32768,eng);
  if(treew && treew[0] && type[0] && !strcmp(type,"content")){ cmd[n++]=L' '; n+=qwl(cmd+n,(int)(32768-n),treew); }
  for(int i=rawi+1;i<argc;i++){ cmd[n++]=L' '; n+=qwl(cmd+n,(int)(32768-n),argv[i]); }
  cmd[n]=0;
  SECURITY_ATTRIBUTES sa={sizeof sa,NULL,TRUE};
  HANDLE rd,wr; CreatePipe(&rd,&wr,&sa,0); SetHandleInformation(rd,HANDLE_FLAG_INHERIT,HANDLE_FLAG_INHERIT);
  STARTUPINFOW si; PROCESS_INFORMATION pi; memset(&si,0,sizeof si); memset(&pi,0,sizeof pi);
  si.cb=sizeof si; si.dwFlags=STARTF_USESTDHANDLES; si.hStdOutput=wr; si.hStdError=GetStdHandle(STD_ERROR_HANDLE);
  if(!CreateProcessW(NULL,cmd,NULL,NULL,TRUE,0x08000000,NULL,NULL,&si,&pi)){ fwprintf(stderr,L"raw spawn failed\n"); exit(1); }
  CloseHandle(wr);
  static char buf[20480]; DWORD got,total=0;
  while(total<sizeof buf-1 && ReadFile(rd,buf+total,sizeof buf-1-total,&got,NULL)&&got) total+=got;
  buf[total]=0; DWORD ec=0; WaitForSingleObject(pi.hProcess,30000); GetExitCodeProcess(pi.hProcess,&ec);
  CloseHandle(pi.hProcess); CloseHandle(pi.hThread); CloseHandle(rd);
  fputs(buf,stdout); if(total&&buf[total-1]!='\n')putchar('\n');
  exit((int)ec);
}
int wmain(int argc, wchar_t **argv){
  if(argc<2){ printf("usage: searchc \"<query>\" [--tree P] [--type name|content|meta] [--budget MS] [--limit N] [--fresh] [--hidden] [--fuzzy] [--json] | health | stop\n"); return 1; }
  char type[16]={0}, q[8192]={0}, tree[2048]={0};
  wchar_t treew[MAX_PATH]={0};
  long budget=2000, limit=50;
  int fresh=0, hidden=0, fuzzy=0, json=0, raw=0, rawi=-1;
  for(int i=1;i<argc;i++){
    if(!wcscmp(argv[i],L"--json")) json=1;
    else if(!wcscmp(argv[i],L"--fresh")) fresh=1;
    else if(!wcscmp(argv[i],L"--hidden")) hidden=1;
    else if(!wcscmp(argv[i],L"--fuzzy")) fuzzy=1;
    else if(!wcscmp(argv[i],L"--content")) strcpy(type,"content");
    else if(!wcscmp(argv[i],L"--meta")) strcpy(type,"meta");
    else if(!wcscmp(argv[i],L"--raw")){ raw=1; rawi=i; }
    else if(!wcscmp(argv[i],L"--tree")&&i+1<argc){ wcscpy(treew,argv[++i]); w2u(treew,tree,sizeof tree); }
    else if(!wcscmp(argv[i],L"--type")&&i+1<argc) w2u(argv[++i],type,sizeof type);
    else if(!wcscmp(argv[i],L"--budget")&&i+1<argc) budget=_wtol(argv[++i]);
    else if(!wcscmp(argv[i],L"--limit")&&i+1<argc) limit=_wtol(argv[++i]);
    else if(!q[0]) w2u(argv[i],q,sizeof q);
  }
  if(raw){ run_raw(argc,argv,rawi,type,treew); return 0; }
  char req[16384], eq[16384], et[4096];
  if(!wcscmp(argv[1],L"health")){ strcpy(req,"{\"cmd\":\"health\"}"); }
  else if(!wcscmp(argv[1],L"stop")){ strcpy(req,"{\"cmd\":\"stop\"}"); }
  else{
    esc_json(q,eq,sizeof eq);
    if(tree[0]) esc_json(tree,et,sizeof et);
    int n=_snprintf(req,sizeof req,"{\"q\":\"%s\",\"budget_ms\":%ld,\"limit\":%ld",eq,budget,limit);
    if(tree[0]) n+=_snprintf(req+n,sizeof req-n,",\"tree\":\"%s\"",et);
    if(type[0]) n+=_snprintf(req+n,sizeof req-n,",\"type\":\"%s\"",type);
    if(fresh) n+=_snprintf(req+n,sizeof req-n,",\"fresh\":true");
    if(hidden) n+=_snprintf(req+n,sizeof req-n,",\"hidden\":true");
    if(fuzzy) n+=_snprintf(req+n,sizeof req-n,",\"fuzzy\":true");
    _snprintf(req+n,(int)(sizeof req-n),"}");
  }
  WSADATA w; WSAStartup(MAKEWORD(2,2),&w);
  int s=-1;
  for(int t=0;t<40;t++){
    s=try_connect();
    if(s>=0) break;
    if(t==0) spawn_daemon();
    Sleep(250);
  }
  if(s<0){ printf("daemon not running on 127.0.0.1:%d\n",PORT); WSACleanup(); return 2; }
  char pkt[16400]; int rl=_snprintf(pkt,sizeof pkt,"%s\n",req);
  if(send(s,pkt,rl,0)!=rl){ closesocket(s); WSACleanup(); printf("send failed\n"); return 3; }
  static char resp[1048576]; int total=0;
  for(;;){
    if(total>=(int)sizeof resp-1) break;
    int got=recv(s,resp+total,sizeof resp-1-total,0);
    if(got<=0) break;
    total+=got;
    if(resp[total-1]=='\n') break;
  }
  closesocket(s); WSACleanup();
  if(total<0) total=0;
  resp[total]=0;
  if(json) fputs(resp,stdout);
  else print_human(resp);
  return 0;
}
