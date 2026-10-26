"""初中原创补全稿：按明确题目与逐条目标写入独占源文件。

仅更新六册 content、初中 teach 与本文登记的项目；不写汇编产物。
运行：python tools/complete_junior.py [七上/七下/八上/八下/九上/九下]
"""
from __future__ import annotations
import json
import pathlib
import sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.evidence import question_policy
from cnkg.verify import check_question
from tools.teach_cards import check_card
DATA = ROOT / 'data'
TOPICS = {t['id']: t for t in json.loads((DATA/'topics.json').read_text())}
TIERS = json.loads((DATA/'topic_tiers.json').read_text())['topics']
FILES = {'七上':'qishang','七下':'qixia','八上':'bashang','八下':'baxia','九上':'jiushang','九下':'jiuxia'}
RECORDS = {}

def parts(s):
    return s.split('|') if isinstance(s, str) else s

def G(explanation, prompt, reference_answer, criteria):
    return {'explanation': explanation, 'self_check': {'prompt':prompt, 'reference_answer':reference_answer, 'criteria':parts(criteria)}}

def E(prompt, answer, steps, verify=None):
    e = {'prompt':prompt, 'answer':str(answer), 'steps':parts(steps)}
    if verify is not None: e['verify'] = verify
    return e

def N(prompt, answer, verify, explanation):
    return {'type':'numeric','prompt':prompt,'answer':str(answer),'verify':verify,'explanation':explanation}

def S(prompt, answer, explanation, accept=None):
    return {'type':'short','prompt':prompt,'answer':answer,'accept':accept or [answer],'explanation':explanation}

def Q(prompt, options, correct, explanation):
    return {'type':'choice','prompt':prompt,'choices':[{'id':f'o{i+1}','text':s} for i,s in enumerate(parts(options))], 'answer':f'o{correct}', 'explanation':explanation + ' 正确选项内容：' + parts(options)[correct-1] + '。'}

def C(tid, hook, why, usage, steps, example, traps, goals, questions=(), definition=None):
    t=TOPICS[tid]
    RECORDS[tid]={'card':{'schema':1,'review_status':'draft','author':'ai','author_type':'ai','topicId':tid,'name':t['name'],'tier':'core',
      'hook':hook,'definition':definition or t['description'],'why':why,'when_to_use':parts(usage),'steps':parts(steps),
      'worked_example':example,'pitfalls':parts(traps),'check':'独立完成并解释：'+'；'.join(g['self_check']['prompt'] for g in goals),
      'objective_coverage':[{'criterion_index':i,'title':t['mastery_criteria'][i],**g} for i,g in enumerate(goals)]},'questions':list(questions)}

def U(tid, goals, questions=()):
    card=json.loads((DATA/'teach'/f'{tid}.json').read_text())
    card['objective_coverage']=[{'criterion_index':i,'title':TOPICS[tid]['mastery_criteria'][i],**g} for i,g in enumerate(goals)]
    RECORDS[tid]={'card':card,'questions':list(questions)}

# 七上：保留现有讲解，并用可观察任务覆盖两条原目标。
U('m7a.1.2.2', [
 G('原点决定零的位置，方向决定正负，单位长度决定一格是多少；同一数轴上的刻度必须均匀。','画数轴，取向右为正，每格表示0.5，标出−1.5、0、2。','原点标0；−1.5在原点左侧3格，2在右侧4格。','有原点、正方向与单位长度|负数在左，三点位置和格数正确'),
 G('读点时先看0和已知刻度，以距离除以单位长度，再按方向给符号。','数轴上0与2相隔4个等长小格，P在0左侧3格，P表示什么数？','每格0.5，P表示−1.5。','由4格对应2得单位为0.5|左侧3格记作−1.5')],
 [N('一条数轴上0与2之间有4个等长小格，点P在原点左侧3格。P表示的数是多少？',-1.5,'-3*(2/4)','每格表示2÷4=0.5；P在左侧，因此P表示−3×0.5=−1.5。')])
U('m7a.1.2.3', [
 G('相反数相加为0；对−a再取相反数得到a，0保持不变。','分别写出−7、2/3和0的相反数。','7、−2/3、0。','三个符号处理正确|说明0的相反数仍为0'),
 G('关于原点对称的两个点到原点距离相等、分居两侧，因此表示相反数。','A表示−4，把A关于原点对称得到B，写出B和AB的长度。','B表示4，AB长8个单位。','使用到原点距离相同且方向相反|AB=4+4=8')],
 [N('数轴上A表示−4，B与A关于原点对称。线段AB长多少个单位？',8,'4+4','关于原点对称得到B表示4；A、B在原点两侧，AB=4+4=8。')])
U('m7a.2.1.1', [
 G('同号合并同方向的量，异号相抵后按剩余量定符号。','计算−8+3，并用数轴移动解释。','−5；从−8向右移动3个单位停在−5。','区分异号相加与绝对值相加|方向与最后位置正确'),
 G('交换律改变次序，结合律改变分组；将相反数或能凑整的数先结合可以少算。','用简便方法计算−17+8+17−3。','(−17+17)+(8−3)=5。','将−17与17配对|其余和为5，说明交换与结合的依据')],
 [N('利用加法交换律与结合律计算：−17+8+17+(−3)。',5,'-17+8+17-3','把互为相反数的−17与17结合，得0；再算8+(−3)=5，所以原式=5。')])
U('m7a.2.1.2', [
 G('减号是运算符，被减数的符号保留；只把减数改为相反数并把运算改成加法。','计算−6−(−9)，写出转为加法的中间式。','−6+(+9)=3。','改运算符和减数的符号|被减数−6不变'),
 G('a−b是与b相加后得到a的数，而a+(−b)再加b也得到a，故二者相同。','解释为什么5−(−2)=7，不能只背诵口诀。','7+(−2)=5，所以5减去−2的结果是7；等价地5+2=7。','用减法与加法的逆关系|验算7+(−2)=5')],
 [N('某地早晨气温−6℃，中午气温3℃。中午比早晨升高多少℃？',9,'3-(-6)','升高量=中午气温−早晨气温=3−(−6)=3+6=9℃。')])
U('m7a.2.2.1', [
 G('非零因子中负因子个数为奇数时积为负，为偶数时积为正；再计算绝对值。','计算(−2)×(−3)×(−4)，先说明符号。','3个负因子，积为负；绝对值2×3×4=24，结果−24。','负因子奇偶判断正确|结果−24'),
 G('倒数满足乘积等于1，非零分数a/b的倒数为b/a，正负号保持。','求−3/5的倒数，并说明0为什么没有倒数。','−5/3；0乘任何数都为0，不可能为1。','倒数符号为负且乘积1|用乘积解释0无倒数')])
U('m7a.2.2.2', [
 G('除以非零数与乘它的倒数具有相同作用，除法先转为乘法再统一符号。','计算(−12)÷3÷(−2)。','(−12)×1/3×(−1/2)=2。','从左到右或逐项转倒数|两负因子结果为正'),
 G('除数为分数时，整个带符号分数取倒数；只翻转被除数是错误的。','计算(−3/4)÷(−1/2)。','(−3/4)×(−2)=3/2。','除数倒数为−2|被除数不变并得3/2')])
U('m7a.2.3.1', [
 G('幂表示相同因子的连乘，底数包括被括号括住的负号，指数是因子个数。','指出(−2)⁴的底数和指数，并写成连乘。','底数−2，指数4；(−2)×(−2)×(−2)×(−2)。','底数包含负号|写出4个因子'),
 G('负数的偶次幂为正，奇次幂为负；−2⁴中的负号不在底数内。','比较(−2)⁴与−2⁴，分别计算。','(−2)⁴=16，−2⁴=−16。','区分括号的作用|给出16和−16两个结果')],
 [N('计算(−2)⁴−2³的值。（注意括号与运算顺序）',8,'(-2)**4-2**3','先算幂：(−2)⁴=16，2³=8，再相减16−8=8。')])
U('m7a.3.1', [
 G('把数量关系逐层翻译：每份乘份数，总量做和差；先确定字母的意义和单位。','一本本子a元，买3本后用20元付款，写出找回的钱数。','20−3a元（须3a≤20）。','先算3本共3a|找零用付款减费用'),
 G('数字系数写在字母前，乘号可省；除法常写为分式，和差整体相乘需括号。','把“x与2的和的3倍”写成规范代数式，并比较3x+2。','3(x+2)；3x+2只把x乘了3。','括号包含x+2|说明3x+2不符合原意')])
U('m7a.3.2', [
 G('求值时每次出现同一字母都代入同一数，负数必须先括起来。','当a=−2、b=3时，求a²−2b。','(−2)²−2×3=−2。','负数平方加括号|先乘方乘法后减法'),
 G('若题中给出的是一个整体的值，可以把相同结构视为一个量，无须先求每个字母。','已知x−y=4，求3x−3y+2。','3(x−y)+2=3×4+2=14。','提取并识别x−y|整体代入得到14')],
 [N('已知x−y=4，不求x、y各自的值，计算3x−3y+2。',14,'3*4+2','3x−3y+2=3(x−y)+2；把x−y整体替换为4，得3×4+2=14。')])
U('m7a.4.1', [
 G('单项式的系数是数字因数，次数是所有字母指数的和；π是常数。','指出−(3/2)x²y的系数与次数。','系数−3/2，次数2+1=3。','符号包含在系数中|次数将字母指数相加'),
 G('多项式的项带着前面的符号，次数取各项次数的最大值。','指出2x³y−5xy+7的项数与次数。','3项，次数4；各项次数分别为4、2、0。','常数项计为一项|最高次项总次数为4')],
 [N('多项式2x³y−5xy+7的次数是多少？',4,'3+1','最高次项是2x³y，其总次数为3+1=4；不是只看x的指数3。')])
U('m7a.4.2', [
 G('同类项的字母及各字母指数完全相同，合并只加减系数，不改变字母部分。','化简3a²b−5ab²+2a²b。','5a²b−5ab²。','合并第一与第三项|ab²和a²b不能合并'),
 G('括号前减号等于整体乘−1，分配到括号内每一项都要变号。','化简2x−(3x−4)，并说明去括号依据。','2x−3x+4=−x+4；依据−1的分配律。','括号内两项都变号|合并得−x+4')])
U('m7a.5.1.1', [
 G('先化简，再核查整式、一个未知数和最高次数为1；未知数在分母内不属于一元一次方程。','判断2x+1=x+5与1/x=2是否为一元一次方程，说明理由。','前者化简为x=4，符合；后者未知数在分母中，不是整式方程。','判断先化简|分别核查次数与整式条件'),
 G('方程的解应使原等式左右相等，代入时必须计算两边。','检验x=3是否为2(x−1)=x+1的解。','左边2(3−1)=4，右边3+1=4，是解。','计算原方程左右两边|相等后才作判断')],
 [Q('下列方程中，化简后是一元一次方程的是哪一个？','2x+3=x+7|x²=4|1/x=3|x+y=2',1,'2x+3=x+7化简为x=4，是只含一个未知数且次数为1的整式方程；其余分别含二次项、分式或两个未知数。')])
U('m7a.5.1.2', [
 G('等式两边同时加减同一个数仍相等；同时乘同一个数或同除以非零数也仍相等。','从3x−2=10得到3x=12，再得到x=4，分别用了什么性质？','先两边同加2，再两边同除以非零数3。','每一步两边做同样运算|说明除数3非零'),
 G('等式性质可以解释移项和系数化1，但不能允许除以0，也不能只改变一边。','有人从2x=2y推得x=y，又从0x=0y推得x=y，两步都成立吗？','第一步除以2成立；第二步不能除以0，且x=1、y=2是反例。','区分非零除数条件|提供反例或清楚说明0x=0y恒成立')])
U('m7a.5.2', [
 G('去分母后先展开，再把同类项归并；每个变形都要与原方程等价，最后回代。','解(x−1)/2+1=3，写完整过程。','两边乘2得x−1+2=6，即x+1=6，x=5；代入(5−1)/2+1=3。','常数项1也乘2|回代原方程检验'),
 G('去分母的乘数作用于等式两边每一项；漏乘没有分母的项会改变原方程。','有人把(x−1)/2+1=3写成x−1+1=6。指出错误并修正。','常数1漏乘2；应为x−1+2=6，所以x=5。','准确指出漏乘的是常数1|修正后的方程和解正确')])
U('m7a.5.3', [
 G('把同一总量的两种表示列为等式，设未知数时写明单位，再验证条件。','3本同价笔记本加2元包装费共20元，列方程并求单价。','设单价x元，则3x+2=20，x=6。','把包装费只加一次|等式与单位正确'),
 G('代数解还需满足人数、长度等情境限制；满足方程不等于能实施方案。','18人每辆小车坐4人，若要求全部车都坐满，方程4x=18的解能表示车辆数吗？','x=4.5不是整数，不能表示车辆数；全部坐满的要求无解。','算出4.5|根据车辆数整数限制解释无可行方案')])
U('m7a.6.1.1', [
 G('立体占有空间，平面图形在平面内；按面、棱、顶点及曲面区分几何体。','比较长方体与圆柱的表面结构。','长方体有6个矩形面、12条棱、8个顶点；圆柱有两个圆形底面和一个曲面。','长方体要素正确|圆柱包含曲面且两个底面相同'),
 G('判断展开图需要折叠后的面位置和连接关系，面积相同并不保证能围成立体。','六个边长相同的正方形排成一条直线，能折成正方体吗？解释。','不能。沿相邻边连续折叠只能绕成侧面，后面的面与已有面重合，无法封住上下两个面。','考虑折叠后的重叠|结论不能而非仅凭6个面判断')],
 [Q('6个同样正方形排成一条直线，只沿公共边折叠，能围成正方体吗？','能，因为恰有6个面|不能，连续折叠会重叠且不能封闭上下两面|能，只要正方形足够小',2,'正方体的6个面必须占据6个不同方向。直条连续折叠会让后续面与前面重叠，不能形成封闭正方体。')])
U('m7a.6.1.2', [
 G('体由面围成，面相交形成线，线相交形成点；同一对象在不同观察尺度下可作不同抽象。','用长方体盒子指出面、线、点各一个，并说明联系。','一个盒面是面，相邻两面的公共棱是线，三条棱相交的顶点是点。','每类对象举例准确|说明相交或围成的关系'),
 G('轨迹描述运动经过的位置：点可形成线，线段平移可扫出面，平面图形运动可形成体。','一条线段沿垂直于自身的方向平移形成什么？一个矩形绕一条边旋转一周呢？','前者扫出矩形面；后者扫出圆柱体。','区分面与体|运动方向或旋转轴说明正确')])
U('m7a.6.2.1', [
 G('线段有两个端点，射线有一个端点，直线无端点；表示射线时端点字母必须放前。','解释射线AB与射线BA的不同，比较线段AB与线段BA。','两射线端点和方向不同；两线段表示同一线段。','射线端点和方向都说明|线段顺序不影响对象'),
 G('两个不同点只属于一条共同直线，因此两个固定支点能确定直线位置，单点不能确定方向。','木工弹直线前为何确定两个不同位置而不是只定一点？','两点确定唯一一条直线；只有一个点，能经过它作无数条方向不同的直线。','条件强调不同的两点|解释单点不能确定方向')],
 [Q('A、B是两个不同的点。下列表示中，哪一对一定表示同一个对象？','射线AB与射线BA|线段AB与线段BA|以A为端点和以B为端点的任意射线',2,'线段由两端点确定，交换书写次序仍是同一线段；射线交换首字母后端点及方向改变。')])
U('m7a.6.2.2', [
 G('圆规保持两针间距可以搬运长度；在指定射线上截取长度时交点唯一。','不用直尺刻度，在射线OX上作OP等于已知线段AB，写出步骤。','以O为圆心、AB为半径画弧，与射线OX交于P，则OP=AB。','圆规开度取AB|在指定射线上取交点'),
 G('中点同时满足在该线段上和分成两条等长线段，结合点序可以把整段写成部分之和。','A、M、B依次共线，M是AB中点，AM=3；求AB。','MB=AM=3，AB=AM+MB=6。','由中点得两半相等|按点序列线段和')],
 [N('A、M、B依次在一直线上，M是AB中点。若AM=3.5厘米，AB长多少厘米？',7,'2*3.5','M在AB上且AM=MB；因此AB=AM+MB=2×3.5=7厘米。')])
U('m7a.6.3.1', [
 G('三字母记角把顶点放中间；只有顶点处唯一一个角时才可只写顶点字母，也可给角编号。','两射线OA、OB组成角，O处没有其他射线，写出三字母法、单字母法和编号法。','∠AOB（或∠BOA）、∠O；图上标1后可写∠1。','三字母顶点居中|单字母记法说明无歧义条件'),
 G('度分秒采用60进制，1°=60′、1′=60″；小数部分必须按60换算。','将12.5°化成度分，再把15′化成度。','12°30′；15′=0.25°。','小数度乘60|分化度除60')])
U('m7a.6.3.2', [
 G('只有射线位于角的内部时，大角才是两个相邻小角之和；先确定相对位置。','OC在∠AOB内部，∠AOB=80°、∠AOC=35°，求∠COB。','∠COB=80°−35°=45°。','利用OC在内部|列角的差正确'),
 G('角平分线把一个角分为两部分且两角相等，必须保留射线在角内部的条件。','OD平分∠AOB，∠AOB=76°，求∠AOD。','∠AOD=∠DOB=76°÷2=38°。','角平分线给出相等关系|正确除以2')],
 [N('OC在∠AOB内部，∠AOB=100°，∠AOC=40°。OD平分∠COB，求∠AOD的度数。',70,'40+(100-40)/2','先得∠COB=100°−40°=60°；OD平分该角，∠COD=30°。OD在OC与OB之间，故∠AOD=40°+30°=70°。')])
U('m7a.6.3.3', [
 G('余角的和是90°，补角的和是180°；给定角大于90°时没有非负的余角。','求35°角的余角与补角。','余角55°，补角145°。','区分两个和|计算90−35与180−35'),
 G('若α+γ=90°且β+γ=90°，两式同减γ得到α=β，这是同角余角相等的依据。','∠1与∠3互余，∠2也与∠3互余。证明∠1=∠2。','∠1=90°−∠3，∠2=90°−∠3，因此∠1=∠2。','写出两条互余等式|由相同差推出相等')],
 [N('一个角的补角比它的余角大多少度？（该角为锐角）',90,'180-90','设原角为α，差为(180°−α)−(90°−α)=90°；α项抵消，结果与原角大小无关。')])

def K(question, indices, note, local_only=False):
    question['tested_criterion_indices']=indices
    question['alignment_note']=note
    if local_only: question['diagnosis_eligible']=False
    return question

def J(explanation, question, criteria):
    prompt=question['prompt']
    if question['type']=='choice':
        prompt += ' 备选：'+'；'.join(f"{chr(65+i)}. {c['text']}" for i,c in enumerate(question['choices']))
    return G(explanation,prompt+' 请写出判断或计算依据。',question['explanation'],criteria)

_SEVEN_A={'m7a.1.2.2':[1],'m7a.1.2.3':[1],'m7a.2.1.1':[1],'m7a.2.1.2':[0],'m7a.2.3.1':[1],'m7a.3.2':[1],'m7a.4.1':[1],'m7a.5.1.1':[0],'m7a.6.1.1':[1],'m7a.6.2.1':[0],'m7a.6.2.2':[1],'m7a.6.3.2':[0,1],'m7a.6.3.3':[0]}
for _tid,_indices in _SEVEN_A.items():
    for _q in RECORDS[_tid]['questions']:
        K(_q,_indices,'本题检验：'+'；'.join(TOPICS[_tid]['mastery_criteria'][i] for i in _indices)+'；过程说明见解析与本卡自检。',_tid.startswith('m7a.6.'))

for _filename in ['junior_cards_qixia.py','junior_cards_bashang.py','junior_cards_baxia.py','junior_cards_jiushang.py','junior_cards_jiuxia.py','junior_objective_refinements.py']:
    _path=ROOT/'tools'/_filename
    if _path.exists(): exec(compile(_path.read_text(),str(_path),'exec'),globals())

def write_volume(volume):
    path=DATA/'content'/f'{FILES[volume]}.json'
    content=json.loads(path.read_text())
    if volume == '九下':
        content['topics']['m9b.33.2.2']['description']=RECORDS['m9b.33.2.2']['card']['definition']
        prefix='在球、轴竖直的直圆柱、轴竖直的直圆锥、各面与投影面平行或垂直的正方体四个候选中，'
        for q in content['questions']:
            if q['id'] in {f'm9b.33.2.2-q{i}' for i in range(1,5)} and not q['prompt'].startswith(prefix):
                q['prompt']=prefix+q['prompt']
                q['clarification_note']='限定候选基本几何体和摆放方向，避免由轮廓无条件断言任意几何体唯一。'
    ids={q['id'] for q in content['questions']}
    selected={tid:r for tid,r in RECORDS.items() if TOPICS[tid]['volume']==volume}
    added=0
    for tid,r in selected.items():
        for i,q in enumerate(r['questions'],201):
            q={**q,'id':f'{tid}-q{i}','topicId':tid,'topicIds':[tid],'primaryTopicId':tid,
               'source':'self-generated-completion','review_status':'draft','author_type':'ai','difficulty':'standard',
               'diagnosis_eligible':False,'for_diagnosis':False,'target_alignment':'component_practice_only',
               'difficulty_basis':'原创补全：需识别本节结构并完成计算或判断；难度仍待课堂校准。'}
            assert 'tested_criterion_indices' in q,(tid,'alignment missing')
            if q['type']=='numeric': assert check_question(q)['status']=='pass',(q,check_question(q))
            assert question_policy(q)['practice_eligible'],(q,question_policy(q))
            if q['id'] not in ids:
                content['questions'].append(q);ids.add(q['id']);added+=1
            else:
                existing=next(x for x in content['questions'] if x['id']==q['id'])
                assert existing.get('source')=='self-generated-completion',q['id']
                existing.update(q)
    path.write_text(json.dumps(content,ensure_ascii=False,indent=1)+'\n')
    bank={q['id']:q for q in json.loads((DATA/'questions.json').read_text())}
    bank.update({q['id']:q for q in content['questions']})
    for tid,r in selected.items():
        card=r['card']
        eligible=[q for q in bank.values() if tid in (q.get('topicIds') or [q.get('topicId')]) and question_policy(q)['practice_eligible']]
        eligible.sort(key=lambda q:(q['type']=='choice',q.get('source')!='self-generated-completion',q['id']))
        card['practice']=[{'id':q['id'],'note':'独立作答后，按解析检查关键条件和推理。'} for q in eligible[:3]]
        assert len(eligible)>=TIERS[tid]['practice_target'],(tid,len(eligible),TIERS[tid]['practice_target'])
        assert len(card['pitfalls'])>=2,(tid,'pitfalls')
        assert len(card['objective_coverage'])==len(TOPICS[tid]['mastery_criteria'])
        errors=check_card(card,TOPICS,bank)
        assert not errors,(tid,errors)
        (DATA/'teach'/f'{tid}.json').write_text(json.dumps(card,ensure_ascii=False,indent=1)+'\n')
    print(volume,': cards=',len(selected),'new_questions=',added,'objective_entries=',sum(len(r['card']['objective_coverage']) for r in selected.values()))

if __name__=='__main__':
    volumes=sys.argv[1:] or list(FILES)
    for volume in volumes: write_volume(volume)
