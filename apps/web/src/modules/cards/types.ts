/**
 * 角色卡模块的对外类型。
 *
 * `description` / `greeting` 是**给编辑用的回显** —— 早期接口只给「名字 + 是否启用」，
 * 因为当时界面只读；现在要能改提示词与开场白，不回显就无从编辑。
 */
export interface CardItem {
  name: string
  /** 是否当前启用的那张 */
  active: boolean
  /** 提示词正文（进提示词的那部分人格描述） */
  description: string
  /** 开场白（first_mes） */
  greeting: string
  /** 卡文件读不出来时的原因；有它说明这张卡坏了，只能在界面上删掉 */
  error?: string
}

/** `/api/pet/cards` 的返回：列表 + 当前启用项（增删改后同样返回这一形状） */
export interface CardsPayload {
  cards: CardItem[]
  /** 当前启用的卡名；null = 不搭载任何角色卡 */
  active: string | null
}
